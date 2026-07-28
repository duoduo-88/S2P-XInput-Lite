"""Early-startup capture and one-file support-log export helpers."""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import faulthandler
from datetime import datetime
from pathlib import Path

from version import APP_NAME, VERSION


STARTUP_LOG_PATH_ENV = "S2P_STARTUP_LOG_PATH"
SUPPORT_LOG_VERSION = 1
STARTUP_LOG_VERSION = 1
MAX_EMBEDDED_STARTUP_LOG_BYTES = 1024 * 1024


def _timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _default_log_directory(environment=None):
    environment = os.environ if environment is None else environment
    local_app_data = str(environment.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data) / APP_NAME / "logs"
    return Path(tempfile.gettempdir()) / APP_NAME / "logs"


def startup_log_paths(environment=None):
    """Return the current and previous startup-log paths."""
    environment = os.environ if environment is None else environment
    explicit = str(environment.get(STARTUP_LOG_PATH_ENV) or "").strip()
    current = (
        Path(explicit)
        if explicit
        else _default_log_directory(environment) / "startup-latest.txt"
    )
    previous_name = (
        current.name.replace("-latest.", "-previous.", 1)
        if "-latest." in current.name
        else "startup-previous.txt"
    )
    return current, current.with_name(previous_name)


def _safe_field(value):
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def write_startup_field(stream, key, value):
    """Write and flush one stable key/value startup record."""
    stream.write(f"{key}={_safe_field(value)}\n")
    stream.flush()


def open_startup_log(base_dir, python_path, gui_path, environment=None):
    """Rotate the previous launch and open a line-buffered current log."""
    environment = os.environ if environment is None else environment
    current, previous = startup_log_paths(environment)
    try:
        current.parent.mkdir(parents=True, exist_ok=True)
        if current.exists():
            current.replace(previous)
        stream = current.open(
            "a",
            encoding="utf-8",
            errors="backslashreplace",
            buffering=1,
        )
    except OSError:
        fallback_environment = dict(environment)
        fallback_environment.pop(STARTUP_LOG_PATH_ENV, None)
        fallback_directory = Path(tempfile.gettempdir()) / APP_NAME / "logs"
        current = fallback_directory / "startup-latest.txt"
        previous = fallback_directory / "startup-previous.txt"
        fallback_directory.mkdir(parents=True, exist_ok=True)
        if current.exists():
            current.replace(previous)
        stream = current.open(
            "a",
            encoding="utf-8",
            errors="backslashreplace",
            buffering=1,
        )

    stream.write("S2P_XINPUT_LITE_STARTUP_LOG\n")
    write_startup_field(stream, "LOG_VERSION", STARTUP_LOG_VERSION)
    write_startup_field(stream, "STARTED_AT", _timestamp())
    write_startup_field(stream, "APP_VERSION", VERSION)
    write_startup_field(stream, "PLATFORM", platform.platform())
    write_startup_field(stream, "LAUNCHER", sys.executable)
    write_startup_field(stream, "BASE_DIR", Path(base_dir))
    write_startup_field(stream, "PYTHON", Path(python_path))
    write_startup_field(stream, "GUI_SCRIPT", Path(gui_path))
    write_startup_field(stream, "PHASE", "launcher_ready")
    stream.write("\n[GUI_OUTPUT]\n")
    stream.flush()
    return stream, current


def attach_gui_startup_capture(gui_path, environment=None):
    """Attach GUI stdout/stderr even when an older launcher starts pythonw."""
    environment = os.environ if environment is None else environment
    explicit = str(environment.get(STARTUP_LOG_PATH_ENV) or "").strip()
    if explicit:
        current = Path(explicit)
        current.parent.mkdir(parents=True, exist_ok=True)
        stream = current.open(
            "a",
            encoding="utf-8",
            errors="backslashreplace",
            buffering=1,
        )
    else:
        gui_path = Path(gui_path).resolve()
        base_dir = gui_path.parent.parent
        stream, current = open_startup_log(
            base_dir,
            sys.executable,
            gui_path,
            environment,
        )
        if environment is os.environ:
            os.environ[STARTUP_LOG_PATH_ENV] = str(current)

    sys.stdout = stream
    sys.stderr = stream
    try:
        faulthandler.enable(file=stream, all_threads=True)
    except (OSError, RuntimeError):
        pass
    write_startup_field(stream, "PHASE", "config_gui_importing")
    return stream


def _read_bounded_text(path):
    try:
        payload = Path(path).read_bytes()
    except OSError:
        return None
    limit = MAX_EMBEDDED_STARTUP_LOG_BYTES
    if len(payload) > limit:
        head_size = limit // 4
        tail_size = limit - head_size
        payload = (
            payload[:head_size]
            + b"\n\n[...STARTUP LOG TRUNCATED...]\n\n"
            + payload[-tail_size:]
        )
    return payload.decode("utf-8", errors="replace").rstrip()


def format_support_log(diagnostic_log=None, environment=None):
    """Combine system, startup, and optional controller diagnostics."""
    current, previous = startup_log_paths(environment)
    lines = [
        "S2P_XINPUT_LITE_SUPPORT_LOG",
        f"LOG_VERSION={SUPPORT_LOG_VERSION}",
        f"GENERATED_AT={_timestamp()}",
        f"APP_VERSION={VERSION}",
        f"PLATFORM={platform.platform()}",
        f"PYTHON={platform.python_version()}",
        f"FROZEN={int(bool(getattr(sys, 'frozen', False)))}",
    ]

    for title, path in (
        ("STARTUP_LOG_CURRENT", current),
        ("STARTUP_LOG_PREVIOUS", previous),
    ):
        content = _read_bounded_text(path)
        lines.extend(("", f"[{title}]", f"PATH={path}"))
        if content is None:
            lines.append("STATUS=NOT_AVAILABLE")
        else:
            lines.extend(("STATUS=AVAILABLE", "", content))

    lines.extend(("", "[CONTROLLER_DIAGNOSTIC]"))
    if diagnostic_log:
        lines.extend(("STATUS=AVAILABLE", "", str(diagnostic_log).rstrip()))
    else:
        lines.append("STATUS=NOT_RUN")
    lines.append("")
    return "\n".join(lines)
