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
#define STREAM_AXES_ALL 0x0FUL
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

LONG _InterlockedExchange(volatile LONG *, LONG);
LONGLONG _InterlockedExchange64(volatile LONGLONG *, LONGLONG);
LONGLONG _InterlockedIncrement64(volatile LONGLONG *);
