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
            float left_x = 0.0f;
            float left_y = 0.0f;
            float right_x = 0.0f;
            float right_y = 0.0f;
            float left_trigger = 0.0f;
            float right_trigger = 0.0f;
            DWORD buttons = 0;
            StreamSlot *slot;
            if (!parse_report(
                path, report, bytes_read,
                &left_x, &left_y, &right_x, &right_y,
                &left_trigger, &right_trigger, &buttons
            )) continue;
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
            slot->reserved = STREAM_AXES_ALL;
            _InterlockedExchange64(&slot->sequence, sequence);
            _InterlockedExchange64(&header->reports, sequence);
            _InterlockedExchange64(&header->latest_sequence, sequence);
        }
    }

finished:
    CancelIoEx(device, 0);
    if (event) CloseHandle(event);
    if (device != INVALID_HANDLE_VALUE) CloseHandle(device);
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
