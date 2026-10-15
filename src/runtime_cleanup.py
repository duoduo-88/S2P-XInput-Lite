"""Small, dependency-free helpers for runtime readiness and safe teardown."""
import ctypes
from ctypes import wintypes
import os
import threading


def watch_parent_process_exit(pid, callback):
    """Wait on the GUI's Windows process object without polling or console IO."""
    if os.name != "nt" or not pid:
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE only
    if not handle:
        if ctypes.get_last_error() == 87:  # parent exited before OpenProcess
            callback()
        return None

    def wait():
        try:
            if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
                callback()
        finally:
            kernel.CloseHandle(handle)

    watcher = threading.Thread(target=wait, name="BridgeGuiLifetime", daemon=True)
    watcher.start()
    return watcher


def controller_application_ready(controller):
    """Use transport readiness, not merely a physical-link indication."""
    return bool(getattr(controller, "is_ready", False))


def close_xinput_after_dispatcher(
    input_dispatcher,
    xinput,
    stop_timeout=1.0,
    grace_timeout=1.0,
):
    """Never mutate XInput state while a claimed input callback may use it."""
    dispatcher_stopped = True
    if input_dispatcher is not None:
        dispatcher_stopped = input_dispatcher.stop(timeout=stop_timeout)
        if not dispatcher_stopped and grace_timeout > 0:
            dispatcher_stopped = input_dispatcher.stop(
                timeout=grace_timeout
            )
    if dispatcher_stopped:
        return xinput.close() is not False

    # Stop the physical-rumble producer, but leave virtual-pad state intact:
    # the timed-out input callback may still be inside handle_input().
    stop_rumble = getattr(xinput, "stop_rumble_dispatcher", None)
    if stop_rumble is not None:
        stop_rumble(timeout=grace_timeout)
    return False
