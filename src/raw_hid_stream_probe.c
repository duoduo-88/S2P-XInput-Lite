/*
 * Fixed-layout Raw HID stream helper for XInput-compatible HID collections.
 *
 * This companion intentionally handles only layouts that are known to expose
 * XInput-style stick reports. The existing raw_hid_probe.exe remains the
 * generic HID-descriptor parser and the finite report-rate measurement tool.
 *
 * Built without the Microsoft C runtime so the executable stays portable.
 */

typedef void *HANDLE;
typedef void *LPVOID;
typedef const void *LPCVOID;
typedef unsigned char BYTE;
typedef unsigned short WORD;
typedef unsigned long DWORD;
typedef long LONG;
typedef unsigned long ULONG;
typedef unsigned long long ULONGLONG;
typedef long long LONGLONG;
typedef unsigned long long ULONG_PTR;
typedef int BOOL;
typedef unsigned char BOOLEAN;
typedef unsigned short WCHAR;
typedef const WCHAR *LPCWSTR;
typedef WCHAR *LPWSTR;
typedef char CHAR;

#define WINAPI __stdcall
#define DLLIMPORT __declspec(dllimport)
#define TRUE 1
#define FALSE 0
#define INVALID_HANDLE_VALUE ((HANDLE)(LONGLONG)-1)
#define NULL_HANDLE ((HANDLE)0)

#define FILE_MAP_ALL_ACCESS 0x000F001FUL
#define GENERIC_READ 0x80000000UL
#define FILE_SHARE_READ 0x00000001UL
#define FILE_SHARE_WRITE 0x00000002UL
#define OPEN_EXISTING 3UL
#define FILE_FLAG_OVERLAPPED 0x40000000UL
#define WAIT_OBJECT_0 0UL
#define WAIT_TIMEOUT 258UL
#define INFINITE 0xFFFFFFFFUL
#define ERROR_IO_PENDING 997UL
#define ERROR_OPERATION_ABORTED 995UL
#define ERROR_BROKEN_PIPE 109UL
#define ERROR_PIPE_NOT_CONNECTED 233UL
#define ERROR_INVALID_PARAMETER 87UL
#define ERROR_INVALID_DATA 13UL
#define STD_INPUT_HANDLE ((DWORD)-10)
#define THREAD_PRIORITY_HIGHEST 2

#define STREAM_MAGIC 0x53524853UL
#define STREAM_VERSION 1UL
#define STREAM_STICK_AXES 0x0FUL
#define STREAM_TRIGGER_AXES 0x30UL
#define MAX_CAPACITY 262144UL
#define MIN_CAPACITY 1024UL
#define REPORT_BUFFER_SIZE 512UL
#define HIDP_STATUS_SUCCESS 0x00110000L

typedef struct StreamHeader {
    DWORD magic;
    DWORD version;
    DWORD header_size;
    DWORD slot_size;
    DWORD capacity;
    volatile LONG state;
    volatile LONG error_code;
    DWORD axes_mask;
    volatile LONGLONG latest_sequence;
    volatile LONGLONG reports;
    ULONGLONG qpc_frequency;
    volatile ULONGLONG reserved;
} StreamHeader;

typedef struct StreamSlot {
    volatile LONGLONG sequence;
    ULONGLONG timestamp_ticks;
    float left_x;
    float left_y;
    float right_x;
    float right_y;
    float left_trigger;
    float right_trigger;
    DWORD buttons;
    DWORD reserved;
} StreamSlot;
typedef char stream_header_size_must_be_64[
    sizeof(StreamHeader) == 64 ? 1 : -1
];
typedef char stream_slot_size_must_be_48[
    sizeof(StreamSlot) == 48 ? 1 : -1
];

typedef union OffsetUnion {
    struct {
        DWORD Offset;
        DWORD OffsetHigh;
    } value;
    void *Pointer;
} OffsetUnion;

typedef struct OVERLAPPED_MIN {
    ULONG_PTR Internal;
    ULONG_PTR InternalHigh;
    OffsetUnion offset;
    HANDLE hEvent;
} OVERLAPPED_MIN;

typedef struct LARGE_INTEGER_MIN {
    LONGLONG QuadPart;
} LARGE_INTEGER_MIN;
typedef struct HIDP_CAPS_MIN {
    WORD Usage;
    WORD UsagePage;
    WORD InputReportByteLength;
    WORD OutputReportByteLength;
    WORD FeatureReportByteLength;
    WORD Reserved[17];
    WORD NumberLinkCollectionNodes;
    WORD NumberInputButtonCaps;
    WORD NumberInputValueCaps;
    WORD NumberInputDataIndices;
    WORD NumberOutputButtonCaps;
    WORD NumberOutputValueCaps;
    WORD NumberOutputDataIndices;
    WORD NumberFeatureButtonCaps;
    WORD NumberFeatureValueCaps;
    WORD NumberFeatureDataIndices;
} HIDP_CAPS_MIN;

DLLIMPORT LPWSTR WINAPI GetCommandLineW(void);
DLLIMPORT void WINAPI ExitProcess(DWORD);
DLLIMPORT HANDLE WINAPI OpenFileMappingW(DWORD, BOOL, LPCWSTR);
DLLIMPORT LPVOID WINAPI MapViewOfFile(HANDLE, DWORD, DWORD, DWORD, ULONG_PTR);
DLLIMPORT BOOL WINAPI UnmapViewOfFile(LPCVOID);
DLLIMPORT BOOL WINAPI CloseHandle(HANDLE);
DLLIMPORT HANDLE WINAPI CreateFileW(LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE);
DLLIMPORT HANDLE WINAPI CreateEventW(LPVOID, BOOL, BOOL, LPCWSTR);
DLLIMPORT BOOL WINAPI ResetEvent(HANDLE);
DLLIMPORT BOOL WINAPI ReadFile(HANDLE, LPVOID, DWORD, DWORD *, OVERLAPPED_MIN *);
DLLIMPORT DWORD WINAPI GetLastError(void);
DLLIMPORT DWORD WINAPI WaitForSingleObject(HANDLE, DWORD);
DLLIMPORT BOOL WINAPI GetOverlappedResult(HANDLE, OVERLAPPED_MIN *, DWORD *, BOOL);
DLLIMPORT BOOL WINAPI CancelIoEx(HANDLE, OVERLAPPED_MIN *);
DLLIMPORT BOOL WINAPI QueryPerformanceCounter(LARGE_INTEGER_MIN *);
DLLIMPORT BOOL WINAPI QueryPerformanceFrequency(LARGE_INTEGER_MIN *);
DLLIMPORT HANDLE WINAPI GetStdHandle(DWORD);
DLLIMPORT BOOL WINAPI PeekNamedPipe(HANDLE, LPVOID, DWORD, DWORD *, DWORD *, DWORD *);
DLLIMPORT BOOL WINAPI SetThreadPriority(HANDLE, int);
DLLIMPORT HANDLE WINAPI GetCurrentThread(void);
DLLIMPORT BOOLEAN WINAPI HidD_SetNumInputBuffers(HANDLE, ULONG);
DLLIMPORT BOOLEAN WINAPI HidD_GetPreparsedData(HANDLE, void **);
DLLIMPORT BOOLEAN WINAPI HidD_FreePreparsedData(void *);
DLLIMPORT LONG WINAPI HidP_GetCaps(void *, HIDP_CAPS_MIN *);
DLLIMPORT LONG WINAPI HidP_GetUsages(
    int, WORD, WORD, WORD *, ULONG *, void *, CHAR *, ULONG
);
DLLIMPORT LONG WINAPI HidP_GetUsageValue(
    int, WORD, WORD, WORD, ULONG *, void *, CHAR *, ULONG
);
DLLIMPORT LONG WINAPI HidP_GetSpecificValueCaps(
    int, WORD, WORD, WORD, void *, WORD *, void *
);

#define HIDP_INPUT 0
#define HID_PAGE_GENERIC_DESKTOP 0x01
#define HID_PAGE_BUTTON 0x09
#define HID_USAGE_Z 0x32
#define HID_USAGE_HAT_SWITCH 0x39
#define XUSB_HID_AXES_END 9UL
#define XUSB_HID_MAX_BUTTONS 16

/* HIDP_VALUE_CAPS: only the logical range is read, at bytes 40..47. */
typedef struct HIDP_VALUE_CAPS_MIN {
    BYTE leading[40];
    LONG LogicalMin;
    LONG LogicalMax;
    BYTE trailing[24];
} HIDP_VALUE_CAPS_MIN;
typedef char hidp_value_caps_size_must_be_72[
    sizeof(HIDP_VALUE_CAPS_MIN) == 72 ? 1 : -1
];

/*
 * Controls of Windows' XInput HID collection (&IG_). Its descriptor lists
 * Button 1..10 (A, B, X, Y, LB, RB, Back, Start, LS, RS), an eight-way hat
 * and both triggers combined on one Z axis, and these are read through
 * HidP so their bit positions never have to be assumed.
 */
typedef struct XusbHidControls {
    void *preparsed;
    int has_hat;
    LONG hat_minimum;
    LONG hat_maximum;
    int has_z;
    DWORD last_length;
    BYTE last_tail[REPORT_BUFFER_SIZE];
    DWORD buttons;
    float left_trigger;
    float right_trigger;
} XusbHidControls;

static XusbHidControls g_xusb;

LONG _InterlockedExchange(volatile LONG *, LONG);
LONGLONG _InterlockedExchange64(volatile LONGLONG *, LONGLONG);
LONGLONG _InterlockedIncrement64(volatile LONGLONG *);
#pragma intrinsic(_InterlockedExchange)
#pragma intrinsic(_InterlockedExchange64)
#pragma intrinsic(_InterlockedIncrement64)

int _fltused = 0;

void *memset(void *destination, int value, ULONG_PTR size) {
    BYTE *bytes = (BYTE *)destination;
    ULONG_PTR index;
    for (index = 0; index < size; ++index) bytes[index] = (BYTE)value;
    return destination;
}

void *memcpy(void *destination, const void *source, ULONG_PTR size) {
    BYTE *target = (BYTE *)destination;
    const BYTE *bytes = (const BYTE *)source;
    ULONG_PTR index;
    for (index = 0; index < size; ++index) target[index] = bytes[index];
    return destination;
}

static void zero_bytes(void *destination, ULONG_PTR size) {
    BYTE *bytes = (BYTE *)destination;
    ULONG_PTR index;
    for (index = 0; index < size; ++index) bytes[index] = 0;
}

static WCHAR upper_ascii(WCHAR value) {
    if (value >= (WCHAR)'a' && value <= (WCHAR)'z') {
        return (WCHAR)(value - (WCHAR)'a' + (WCHAR)'A');
    }
    return value;
}

static int wide_equal_ascii(LPCWSTR value, const char *ascii) {
    ULONG_PTR index = 0;
    if (!value || !ascii) return 0;
    while (ascii[index] != 0 && value[index] != 0) {
        if (value[index] != (WCHAR)(unsigned char)ascii[index]) return 0;
        ++index;
    }
    return ascii[index] == 0 && value[index] == 0;
}

static int contains_ascii_ci(LPCWSTR value, const char *token) {
    ULONG_PTR start;
    ULONG_PTR token_length = 0;
    if (!value || !token || token[0] == 0) return 0;
    while (token[token_length] != 0) ++token_length;
    for (start = 0; value[start] != 0; ++start) {
        ULONG_PTR index = 0;
        while (
            index < token_length
            && value[start + index] != 0
            && upper_ascii(value[start + index])
                == upper_ascii((WCHAR)(unsigned char)token[index])
        ) {
            ++index;
        }
        if (index == token_length) return 1;
    }
    return 0;
}

static LPCWSTR skip_spaces(LPCWSTR cursor) {
    while (*cursor == (WCHAR)' ' || *cursor == (WCHAR)'\t') ++cursor;
    return cursor;
}

static LPCWSTR copy_next_argument(
    LPCWSTR cursor,
    LPWSTR destination,
    ULONG_PTR capacity
) {
    ULONG_PTR length = 0;
    int quoted = 0;
    cursor = skip_spaces(cursor);
    if (*cursor == (WCHAR)'"') {
        quoted = 1;
        ++cursor;
    }
    while (*cursor != 0) {
        if (quoted) {
            if (*cursor == (WCHAR)'"') {
                ++cursor;
                break;
            }
        } else if (*cursor == (WCHAR)' ' || *cursor == (WCHAR)'\t') {
            break;
        }
        if (length + 1 < capacity) destination[length++] = *cursor;
        ++cursor;
    }
    if (capacity) destination[length] = 0;
    return skip_spaces(cursor);
}

static DWORD parse_u32(LPCWSTR value) {
    ULONGLONG result = 0;
    ULONG_PTR index = 0;
    if (!value || value[0] == 0) return 0;
    while (value[index] >= (WCHAR)'0' && value[index] <= (WCHAR)'9') {
        result = result * 10ULL + (ULONGLONG)(value[index] - (WCHAR)'0');
        if (result > 0xFFFFFFFFULL) return 0;
        ++index;
    }
    return value[index] == 0 ? (DWORD)result : 0;
}

static ULONGLONG qpc_now(void) {
    LARGE_INTEGER_MIN value;
    value.QuadPart = 0;
    QueryPerformanceCounter(&value);
    return (ULONGLONG)value.QuadPart;
}

static int stop_requested(void) {
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    DWORD available = 0;
    DWORD read = 0;
    CHAR buffer[64];
    DWORD index;
    if (!input || input == INVALID_HANDLE_VALUE) return 0;
    if (!PeekNamedPipe(input, 0, 0, 0, &available, 0)) {
        DWORD error = GetLastError();
        return error == ERROR_BROKEN_PIPE || error == ERROR_PIPE_NOT_CONNECTED;
    }
    if (available == 0) return 0;
    if (available > (DWORD)(sizeof(buffer) - 1)) {
        available = (DWORD)(sizeof(buffer) - 1);
    }
    if (!ReadFile(input, buffer, available, &read, 0)) return 0;
    buffer[read] = 0;
    for (index = 0; index + 3 < read; ++index) {
        if (
            (buffer[index] == 's' || buffer[index] == 'S')
            && (buffer[index + 1] == 't' || buffer[index + 1] == 'T')
            && (buffer[index + 2] == 'o' || buffer[index + 2] == 'O')
            && (buffer[index + 3] == 'p' || buffer[index + 3] == 'P')
        ) return 1;
    }
    return 0;
}

static unsigned short read_u16_le(const BYTE *data, DWORD offset) {
    return (unsigned short)(
        (unsigned short)data[offset]
        | ((unsigned short)data[offset + 1] << 8)
    );
}

static short read_i16_le(const BYTE *data, DWORD offset) {
    return (short)read_u16_le(data, offset);
}

static float normalize_unsigned16(unsigned short raw, int invert) {
    float value = ((float)raw / 65535.0f) * 2.0f - 1.0f;
    return invert ? -value : value;
}

static float normalize_signed16(short raw) {
    if (raw >= 0) return (float)raw / 32767.0f;
    return (float)raw / 32768.0f;
}

static const DWORD XUSB_HID_BUTTON_MASKS[10] = {
    0x1000, /* A */
    0x2000, /* B */
    0x4000, /* X */
    0x8000, /* Y */
    0x0100, /* LB */
    0x0200, /* RB */
    0x0020, /* Back */
    0x0010, /* Start */
    0x0040, /* LS */
    0x0080, /* RS */
};

static const DWORD HAT_DPAD_MASKS[8] = {
    0x0001,          /* up */
    0x0001 | 0x0008, /* up-right */
    0x0008,          /* right */
    0x0002 | 0x0008, /* down-right */
    0x0002,          /* down */
    0x0002 | 0x0004, /* down-left */
    0x0004,          /* left */
    0x0001 | 0x0004, /* up-left */
};

static void xusb_hid_open(void *preparsed) {
    HIDP_VALUE_CAPS_MIN value_caps;
    WORD count;
    zero_bytes(&g_xusb, sizeof(g_xusb));
    g_xusb.preparsed = preparsed;
    count = 1;
    if (
        HidP_GetSpecificValueCaps(
            HIDP_INPUT, HID_PAGE_GENERIC_DESKTOP, 0, HID_USAGE_HAT_SWITCH,
            &value_caps, &count, preparsed
        ) == HIDP_STATUS_SUCCESS
        && count == 1
        && value_caps.LogicalMax - value_caps.LogicalMin == 7
    ) {
        g_xusb.has_hat = 1;
        g_xusb.hat_minimum = value_caps.LogicalMin;
        g_xusb.hat_maximum = value_caps.LogicalMax;
    }
    count = 1;
    g_xusb.has_z = HidP_GetSpecificValueCaps(
        HIDP_INPUT, HID_PAGE_GENERIC_DESKTOP, 0, HID_USAGE_Z,
        &value_caps, &count, preparsed
    ) == HIDP_STATUS_SUCCESS && count == 1;
    /* Force the first report to be decoded. */
    g_xusb.last_length = 0xFFFFFFFFUL;
}

static void xusb_hid_decode_controls(const BYTE *report, DWORD length) {
    WORD usages[XUSB_HID_MAX_BUTTONS];
    ULONG usage_count = XUSB_HID_MAX_BUTTONS;
    ULONG value = 0;
    DWORD buttons = 0;
    DWORD index;
    DWORD tail_length;
    int changed = length != g_xusb.last_length;

    /* Sticks come before byte 9. Skip HidP while the rest is unchanged. */
    tail_length = length > XUSB_HID_AXES_END ? length - XUSB_HID_AXES_END : 0;
    for (index = 0; !changed && index < tail_length; ++index) {
        changed = report[XUSB_HID_AXES_END + index] != g_xusb.last_tail[index];
    }
    if (!changed) return;
    g_xusb.last_length = length;
    for (index = 0; index < tail_length; ++index) {
        g_xusb.last_tail[index] = report[XUSB_HID_AXES_END + index];
    }

    if (
        HidP_GetUsages(
            HIDP_INPUT, HID_PAGE_BUTTON, 0, usages, &usage_count,
            g_xusb.preparsed, (CHAR *)report, length
        ) == HIDP_STATUS_SUCCESS
    ) {
        for (index = 0; index < usage_count; ++index) {
            if (usages[index] >= 1 && usages[index] <= 10) {
                buttons |= XUSB_HID_BUTTON_MASKS[usages[index] - 1];
            }
        }
    }
    if (
        g_xusb.has_hat
        && HidP_GetUsageValue(
            HIDP_INPUT, HID_PAGE_GENERIC_DESKTOP, 0, HID_USAGE_HAT_SWITCH,
            &value, g_xusb.preparsed, (CHAR *)report, length
        ) == HIDP_STATUS_SUCCESS
        && (LONG)value >= g_xusb.hat_minimum
        && (LONG)value <= g_xusb.hat_maximum
    ) {
        /* Values outside the logical range are the centered null state. */
        buttons |= HAT_DPAD_MASKS[(LONG)value - g_xusb.hat_minimum];
    }
    g_xusb.buttons = buttons;

    /*
     * Both triggers share Z, centered at 0x8000: the left trigger raises it
     * and the right trigger lowers it, so pressing both cancels out. Only
     * the net direction can be shown from this collection.
     */
    g_xusb.left_trigger = 0.0f;
    g_xusb.right_trigger = 0.0f;
    value = 0;
    if (
        g_xusb.has_z
        && HidP_GetUsageValue(
            HIDP_INPUT, HID_PAGE_GENERIC_DESKTOP, 0, HID_USAGE_Z,
            &value, g_xusb.preparsed, (CHAR *)report, length
        ) == HIDP_STATUS_SUCCESS
    ) {
        LONG offset = (LONG)(value & 0xFFFFUL) - 0x8000L;
        if (offset > 0) {
            g_xusb.left_trigger = (float)offset / 32767.0f;
        } else if (offset < 0) {
            g_xusb.right_trigger = (float)(-offset) / 32768.0f;
        }
    }
}

static DWORD parse_report(
    LPCWSTR path,
    const BYTE *report,
    DWORD length,
    float *left_x,
    float *left_y,
    float *right_x,
    float *right_y,
    float *left_trigger,
    float *right_trigger,
    DWORD *buttons
) {
    const int cafe_xinput = contains_ascii_ci(path, "VID_CAFE&PID_4020");
    const int xinput_hid = contains_ascii_ci(path, "&IG_");
    /* Direct 20-byte XUSB endpoint layout used by the standalone firmware. */
    if (cafe_xinput && length == 20 && report[1] == 20) {
        *buttons = (DWORD)report[2] | ((DWORD)report[3] << 8);
        *left_trigger = (float)report[4] / 255.0f;
        *right_trigger = (float)report[5] / 255.0f;
        *left_x = normalize_signed16(read_i16_le(report, 6));
        *left_y = normalize_signed16(read_i16_le(report, 8));
        *right_x = normalize_signed16(read_i16_le(report, 10));
        *right_y = normalize_signed16(read_i16_le(report, 12));
        return STREAM_STICK_AXES | STREAM_TRIGGER_AXES;
    }
    /*
     * Windows' XInput HID collection (&IG_) exposes four unsigned 16-bit axes
     * after the report ID. This is the same layout already verified by the
     * project's original helper for VID_413D&PID_2104.
     */
    if (
        length >= 9
        && (
            xinput_hid
            || cafe_xinput
            || contains_ascii_ci(path, "ROOT#VIGEM")
        )
    ) {
        *left_x = normalize_unsigned16(read_u16_le(report, 1), 0);
        *left_y = normalize_unsigned16(read_u16_le(report, 3), 1);
        *right_x = normalize_unsigned16(read_u16_le(report, 5), 0);
        *right_y = normalize_unsigned16(read_u16_le(report, 7), 1);
        if (g_xusb.preparsed) {
            xusb_hid_decode_controls(report, length);
            *left_trigger = g_xusb.left_trigger;
            *right_trigger = g_xusb.right_trigger;
            *buttons = g_xusb.buttons;
            return g_xusb.has_z
                ? STREAM_STICK_AXES | STREAM_TRIGGER_AXES
                : STREAM_STICK_AXES;
        }
        *left_trigger = 0.0f;
        *right_trigger = 0.0f;
        *buttons = 0;
        return STREAM_STICK_AXES;
    }
    return 0;
}

static DWORD run_stream(
    LPCWSTR path,
    LPCWSTR mapping_name,
    DWORD capacity
) {
    HANDLE mapping = NULL_HANDLE;
    HANDLE device = INVALID_HANDLE_VALUE;
    HANDLE event = NULL_HANDLE;
    StreamHeader *header;
    StreamSlot *slots;
    ULONG_PTR mapping_size;
    BYTE report[REPORT_BUFFER_SIZE];
    void *preparsed = 0;
    HIDP_CAPS_MIN caps;
    DWORD report_length = 0;
    LARGE_INTEGER_MIN frequency_value;
    ULONGLONG frequency;
    LONGLONG sequence = 0;
    LONGLONG parsed_samples = 0;
    float left_x = 0.0f;
    float left_y = 0.0f;
    float right_x = 0.0f;
    float right_y = 0.0f;
    float left_trigger = 0.0f;
    float right_trigger = 0.0f;
    DWORD buttons = 0;
    DWORD stop_poll_counter = 0;
    ULONGLONG last_stop_poll = 0;
    ULONGLONG stop_poll_interval;
    DWORD result = 0;

    if (capacity < MIN_CAPACITY || capacity > MAX_CAPACITY) return 2;
    mapping_size = (ULONG_PTR)sizeof(StreamHeader)
        + (ULONG_PTR)capacity * (ULONG_PTR)sizeof(StreamSlot);
    mapping = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, mapping_name);
    if (!mapping) return 3;
    header = (StreamHeader *)MapViewOfFile(
        mapping, FILE_MAP_ALL_ACCESS, 0, 0, mapping_size
    );
    if (!header) {
        CloseHandle(mapping);
        return 3;
    }
    zero_bytes(header, mapping_size);
    slots = (StreamSlot *)((BYTE *)header + sizeof(StreamHeader));
    header->magic = STREAM_MAGIC;
    header->version = STREAM_VERSION;
    header->header_size = (DWORD)sizeof(StreamHeader);
    header->slot_size = (DWORD)sizeof(StreamSlot);
    header->capacity = capacity;
    header->axes_mask = STREAM_STICK_AXES;
    frequency_value.QuadPart = 0;
    if (!QueryPerformanceFrequency(&frequency_value) || frequency_value.QuadPart <= 0) {
        header->error_code = ERROR_INVALID_DATA;
        header->state = 3;
        UnmapViewOfFile(header);
        CloseHandle(mapping);
        return 1;
    }
    frequency = (ULONGLONG)frequency_value.QuadPart;
    header->qpc_frequency = frequency;

    if (
        !contains_ascii_ci(path, "VID_CAFE&PID_4020")
        && !contains_ascii_ci(path, "VID_413D&PID_2104")
        && !contains_ascii_ci(path, "VID_045E&PID_028E")
        && !contains_ascii_ci(path, "ROOT#VIGEM")
    ) {
        header->error_code = ERROR_INVALID_PARAMETER;
        header->state = 3;
        UnmapViewOfFile(header);
        CloseHandle(mapping);
        return 1;
    }

    device = CreateFileW(
        path,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        0,
        OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED,
        0
    );
    if (device == INVALID_HANDLE_VALUE) {
        header->error_code = (LONG)GetLastError();
        header->state = 3;
        UnmapViewOfFile(header);
        CloseHandle(mapping);
        return 1;
    }
    zero_bytes(&caps, sizeof(caps));
    if (
        !HidD_GetPreparsedData(device, &preparsed)
        || HidP_GetCaps(preparsed, &caps) != HIDP_STATUS_SUCCESS
        || caps.InputReportByteLength == 0
        || caps.InputReportByteLength > REPORT_BUFFER_SIZE
    ) {
        header->error_code = ERROR_INVALID_DATA;
        header->state = 3;
        if (preparsed) HidD_FreePreparsedData(preparsed);
        CloseHandle(device);
        UnmapViewOfFile(header);
        CloseHandle(mapping);
        return 1;
    }
    report_length = (DWORD)caps.InputReportByteLength;
    if (contains_ascii_ci(path, "&IG_")) {
        /* Kept until the stream ends to read buttons, hat and triggers. */
        xusb_hid_open(preparsed);
    } else {
        HidD_FreePreparsedData(preparsed);
    }
    preparsed = 0;
    HidD_SetNumInputBuffers(device, 512);
    event = CreateEventW(0, TRUE, FALSE, 0);
    if (!event) {
        header->error_code = (LONG)GetLastError();
        header->state = 3;
        CloseHandle(device);
        UnmapViewOfFile(header);
        CloseHandle(mapping);
        return 1;
    }
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    _InterlockedExchange(&header->state, 1);
    stop_poll_interval = frequency / 100ULL;
    if (stop_poll_interval == 0) stop_poll_interval = 1;

    for (;;) {
        OVERLAPPED_MIN overlapped;
        DWORD bytes_read = 0;
        BOOL completed;
        ULONGLONG now = qpc_now();
        if (
            ++stop_poll_counter >= 64
            || now - last_stop_poll >= stop_poll_interval
        ) {
            stop_poll_counter = 0;
            last_stop_poll = now;
            if (stop_requested()) break;
        }

        zero_bytes(&overlapped, sizeof(overlapped));
        overlapped.hEvent = event;
        ResetEvent(event);
        completed = ReadFile(
            device,
            report,
            report_length,
            &bytes_read,
            &overlapped
        );
        if (!completed) {
            DWORD error = GetLastError();
            if (error != ERROR_IO_PENDING) {
                header->error_code = (LONG)error;
                _InterlockedExchange(&header->state, 3);
                result = 1;
                break;
            }
            for (;;) {
                DWORD wait = WaitForSingleObject(event, 10);
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
                if (stop_requested()) {
                    CancelIoEx(device, &overlapped);
                    WaitForSingleObject(event, INFINITE);
                    completed = FALSE;
                    result = 0;
                    goto finished;
                }
            }
        }
        if (!completed) {
            DWORD error = GetLastError();
            if (error == ERROR_OPERATION_ABORTED) continue;
            header->error_code = (LONG)error;
            _InterlockedExchange(&header->state, 3);
            result = 1;
            break;
        }
        if (bytes_read == 0) continue;
        _InterlockedIncrement64((volatile LONGLONG *)&header->reserved);
        {
            StreamSlot *slot;
            DWORD sample_axes = parse_report(
                path, report, bytes_read,
                &left_x, &left_y, &right_x, &right_y,
                &left_trigger, &right_trigger, &buttons
            );
            if (sample_axes != 0) ++parsed_samples;
            ++sequence;
            slot = &slots[((ULONGLONG)sequence - 1ULL) % capacity];
            _InterlockedExchange64(&slot->sequence, 0);
            slot->timestamp_ticks = qpc_now();
            slot->left_x = left_x;
            slot->left_y = left_y;
            slot->right_x = right_x;
            slot->right_y = right_y;
            slot->left_trigger = left_trigger;
            slot->right_trigger = right_trigger;
            slot->buttons = buttons;
            slot->reserved = sample_axes;
            _InterlockedExchange64(&slot->sequence, sequence);
            _InterlockedExchange64(&header->reports, parsed_samples);
            _InterlockedExchange64(&header->latest_sequence, sequence);
        }
    }

finished:
    CancelIoEx(device, 0);
    if (event) CloseHandle(event);
    if (device != INVALID_HANDLE_VALUE) CloseHandle(device);
    if (g_xusb.preparsed) {
        HidD_FreePreparsedData(g_xusb.preparsed);
        g_xusb.preparsed = 0;
    }
    if (header->state != 3) _InterlockedExchange(&header->state, 2);
    UnmapViewOfFile(header);
    CloseHandle(mapping);
    return result;
}

static WCHAR g_argument0[1024];
static WCHAR g_argument1[64];
static WCHAR g_path[2048];
static WCHAR g_argument3[64];
static WCHAR g_mapping_name[256];
static WCHAR g_argument5[64];
static WCHAR g_capacity_text[64];

void wmainCRTStartup(void) {
    LPCWSTR cursor = GetCommandLineW();
    DWORD capacity;
    DWORD result;
    cursor = copy_next_argument(cursor, g_argument0, 1024);
    cursor = copy_next_argument(cursor, g_argument1, 64);
    cursor = copy_next_argument(cursor, g_path, 2048);
    cursor = copy_next_argument(cursor, g_argument3, 64);
    cursor = copy_next_argument(cursor, g_mapping_name, 256);
    cursor = copy_next_argument(cursor, g_argument5, 64);
    cursor = copy_next_argument(cursor, g_capacity_text, 64);
    if (
        !wide_equal_ascii(g_argument1, "--stream")
        || !wide_equal_ascii(g_argument3, "--mapping")
        || !wide_equal_ascii(g_argument5, "--capacity")
    ) {
        ExitProcess(2);
    }
    capacity = parse_u32(g_capacity_text);
    result = run_stream(g_path, g_mapping_name, capacity);
    ExitProcess(result);
}
