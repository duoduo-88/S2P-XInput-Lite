        && length == 20 && report[1] == 20
    ) {
        *buttons = (DWORD)report[2] | ((DWORD)report[3] << 8);
        *left_trigger = (float)report[4] / 255.0f;
        *right_trigger = (float)report[5] / 255.0f;
        *left_x = normalize_signed16(read_i16_le(report, 6));
        *left_y = normalize_signed16(read_i16_le(report, 8));
        *right_x = normalize_signed16(read_i16_le(report, 10));
        *right_y = normalize_signed16(read_i16_le(report, 12));
        return 1;
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
        *left_trigger = 0.0f;
        *right_trigger = 0.0f;
        *buttons = 0;
        return 1;
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
    header->axes_mask = STREAM_AXES_ALL;
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
        !contains_ascii_ci(path, "&IG_")
        && !contains_ascii_ci(path, "VID_CAFE&PID_4020")
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
    HidD_FreePreparsedData(preparsed);
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
