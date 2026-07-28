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

static int parse_report(
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
    if (
        cafe_xinput && !xinput_hid
