"""Conservative integration with the official HidHide command-line client."""

import json
import os
import subprocess
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only project
    winreg = None


NINTENDO_USB_HID_PREFIX = "HID\\VID_057E&PID_2069&MI_00\\"
_HIDHIDE_REGISTRY_PATH = r"SOFTWARE\Nefarius Software Solutions e.U.\Nefarius Software Solutions e.U. HidHide"



class HidHideError(RuntimeError):
    """Raised when the official HidHide client cannot read or save settings."""


def locate_hidhide_cli():
    """Return the installed x64 HidHideCLI path without assuming ownership."""
    candidates = []
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, _HIDHIDE_REGISTRY_PATH) as key:
                install_path = winreg.QueryValueEx(key, "Path")[0]
            if install_path:
                root = Path(str(install_path))
                candidates.extend((
                    root / "x64" / "HidHideCLI.exe",
                    root / "HidHideCLI.exe",
                ))
        except OSError:
            pass

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates.append(
        Path(program_files)
        / "Nefarius Software Solutions"
        / "HidHide"
        / "x64"
        / "HidHideCLI.exe"
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _run_cli(cli_path, *arguments):
    try:
        result = subprocess.run(
            [str(cli_path), *map(str, arguments)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HidHideError(str(exc)) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise HidHideError(detail)
    return result.stdout.strip()


def _quoted_values(output, command):
    prefix = command + " \""
    values = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(prefix) and line.endswith('"'):
            values.append(line[len(prefix):-1])
    return values


def _normalize_application_path(value):
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def _application_paths(value):
    """Return distinct absolute application paths from one path or an iterable."""
    if isinstance(value, (str, bytes, os.PathLike)):
        values = (value,)
    else:
        values = tuple(value or ())
    paths = []
    seen = set()
    for item in values:
        path = Path(os.fsdecode(item)).resolve()
        normalized = _normalize_application_path(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        paths.append(path)
    return tuple(paths)


def _preferred_hid_symbolic_link():
    """Match the same first physical controller selected by wired_controller."""
    try:
        import hid
        entries = hid.enumerate(0x057E, 0x2069) or []
    except Exception:
        return None
    entries = [
        item for item in entries
        if "SWITCH2EMU" not in str(item.get("serial_number") or "").upper()
    ]
    entries.sort(key=lambda item: 0 if (
        item.get("usage_page", 0) == 0x01
        and item.get("usage", 0) in (0x04, 0x05)
    ) else 1)
    if not entries:
        return None
    path = entries[0].get("path")
    if isinstance(path, bytes):
        path = path.decode("utf-8", "ignore")
    return str(path or "").casefold() or None


def _physical_nintendo_devices(output, preferred_symbolic_link=None):
    start = output.find("[")
    end = output.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        groups = json.loads(output[start:end + 1])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HidHideError(f"invalid --dev-gaming response: {exc}") from exc

    devices = []
    for group in groups if isinstance(groups, list) else ():
        if not isinstance(group, dict):
            continue
        for device in group.get("devices", ()):
            if not isinstance(device, dict) or not device.get("present"):
                continue
            instance_id = str(device.get("deviceInstancePath") or "").strip()
            if instance_id.upper().startswith(NINTENDO_USB_HID_PREFIX):
                devices.append((
                    instance_id,
                    str(device.get("symbolicLink") or "").casefold(),
                ))
    devices = list(dict.fromkeys(devices))
    if preferred_symbolic_link:
        for instance_id, symbolic_link in devices:
            if symbolic_link == preferred_symbolic_link:
                return [instance_id]
    # The application supports one controller at a time; never hide every
    # identical controller merely because more than one is connected.
    return [devices[0][0]] if devices else []


def inspect_hidhide(application_paths):
    """Read shared HidHide state without modifying any existing configuration."""
    cli_path = locate_hidhide_cli()
    status = {
        "state": "not_installed",
        "installed": False,
        "cli_path": cli_path,
        "cloak_active": False,
        "app_registered": False,
        "application_paths": [],
        "missing_application_paths": [],
        "physical_devices": [],
        "hidden_devices": [],
        "hidden_count": 0,
        "error": None,
    }
    if cli_path is None:
        return status

    status["installed"] = True
    try:
        cloak_output = _run_cli(cli_path, "--cloak-state")
        applications = _quoted_values(
            _run_cli(cli_path, "--app-list"), "--app-reg"
        )
        hidden_devices = _quoted_values(
            _run_cli(cli_path, "--dev-list"), "--dev-hide"
        )
        physical_devices = _physical_nintendo_devices(
            _run_cli(cli_path, "--dev-gaming"),
            _preferred_hid_symbolic_link(),
        )
    except HidHideError as exc:
        status["state"] = "error"
        status["error"] = str(exc)
        return status

    managed_paths = _application_paths(application_paths)
    registered_normalized = {
        _normalize_application_path(path) for path in applications
    }
    missing_paths = [
        path for path in managed_paths
        if _normalize_application_path(path) not in registered_normalized
    ]
    status["cloak_active"] = "--cloak-on" in cloak_output.splitlines()
    status["application_paths"] = [str(path) for path in managed_paths]
    status["missing_application_paths"] = [
        str(path) for path in missing_paths
    ]
    status["app_registered"] = bool(managed_paths) and not missing_paths
    status["physical_devices"] = physical_devices
    status["hidden_devices"] = hidden_devices
    status["hidden_count"] = len(hidden_devices)

    hidden_upper = {value.upper() for value in hidden_devices}
    all_physical_hidden = bool(physical_devices) and all(
        value.upper() in hidden_upper for value in physical_devices
    )
    if not physical_devices:
        status["state"] = "no_device"
    elif not status["cloak_active"]:
        status["state"] = "disabled"
    elif not status["app_registered"] or not all_physical_hidden:
        status["state"] = "setup"
    else:
        status["state"] = "ready"
    return status


def configure_hidhide(application_paths, enable_cloak=True):
    """Add only this app/device to shared lists, then verify the resulting state."""
    managed_paths = _application_paths(application_paths)
    status = inspect_hidhide(managed_paths)
    cli_path = status.get("cli_path")
    if not status.get("installed") or cli_path is None or status.get("error"):
        return status
    if not status.get("physical_devices"):
        return status

    arguments = []
    missing_paths = status.get("missing_application_paths")
    if missing_paths is None:
        missing_paths = (
            [str(path) for path in managed_paths]
            if not status.get("app_registered") else []
        )
    for path in missing_paths:
        arguments.extend(("--app-reg", path))
    hidden_upper = {
        value.upper() for value in status.get("hidden_devices", ())
    }
    for instance_id in status["physical_devices"]:
        if instance_id.upper() not in hidden_upper:
            arguments.extend(("--dev-hide", instance_id))
    if enable_cloak and not status.get("cloak_active"):
        arguments.append("--cloak-on")

    if arguments:
        try:
            _run_cli(cli_path, *arguments)
        except HidHideError as exc:
            status["state"] = "error"
            status["error"] = str(exc)
            return status
    return inspect_hidhide(managed_paths)


def remove_hidhide_configuration(application_paths):
    """Remove this app and the selected controller without touching other entries."""
    managed_paths = _application_paths(application_paths)
    status = inspect_hidhide(managed_paths)
    cli_path = status.get("cli_path")
    if not status.get("installed") or cli_path is None or status.get("error"):
        return status

    selected_upper = {
        value.upper() for value in status.get("physical_devices", ())
    }
    # A disconnected device cannot be identified safely. Never unregister the
    # reader while an unknown hidden device remains, or the next USB connection
    # could become unreadable by this application.
    if not selected_upper and status.get("hidden_devices"):
        status["state"] = "error"
        status["error"] = (
            "Connect the USB controller before resetting HidHide so the "
            "application can identify only its device."
        )
        return status
    arguments = []
    for instance_id in status.get("hidden_devices", ()):
        if instance_id.upper() in selected_upper:
            arguments.extend(("--dev-unhide", instance_id))
    missing_normalized = {
        _normalize_application_path(path)
        for path in status.get("missing_application_paths", ())
    }
    for path in managed_paths:
        if _normalize_application_path(path) not in missing_normalized:
            arguments.extend(("--app-unreg", str(path)))

    remaining_hidden = [
        value for value in status.get("hidden_devices", ())
        if value.upper() not in selected_upper
    ]
    if status.get("cloak_active") and not remaining_hidden:
        arguments.append("--cloak-off")

    if arguments:
        try:
            _run_cli(cli_path, *arguments)
        except HidHideError as exc:
            status["state"] = "error"
            status["error"] = str(exc)
            return status
    return inspect_hidhide(managed_paths)


def reconcile_active_hidhide(application_paths):
    """Repair our entries only when the user has already enabled global hiding."""
    status = inspect_hidhide(application_paths)
    if (
        status.get("installed")
        and status.get("cloak_active")
        and status.get("physical_devices")
        and status.get("state") != "ready"
    ):
        return configure_hidhide(application_paths, enable_cloak=False)
    return status
