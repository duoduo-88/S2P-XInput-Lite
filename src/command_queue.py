"""Crash-safe, one-file-per-request controller command queue."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path


COMMAND_QUEUE_DIR = Path(__file__).with_name("controller_commands")


def enqueue_controller_command(command, queue_dir=COMMAND_QUEUE_DIR):
    directory = Path(queue_dir)
    directory.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex
    payload = {
        "id": request_id,
        "command": str(command).strip(),
        "created_at": time.time(),
    }
    fd, temp_name = tempfile.mkstemp(
        prefix=".command-", suffix=".tmp", dir=str(directory), text=True
    )
    target = directory / f"{time.time_ns():020d}-{request_id}.json"
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return request_id


def next_controller_command(queue_dir=COMMAND_QUEUE_DIR):
    directory = Path(queue_dir)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            command = str(payload.get("command", "")).strip()
            request_id = str(payload.get("id", "")).strip()
            if not command or not request_id:
                path.rename(path.with_suffix(".invalid"))
                continue
            return {"id": request_id, "command": command, "path": path}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                path.rename(path.with_suffix(".invalid"))
            except OSError:
                pass
    return None


def cleanup_controller_commands(not_before, queue_dir=COMMAND_QUEUE_DIR):
    """Discard requests left by an earlier connector process."""
    directory = Path(queue_dir)
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            created_at = float(payload.get("created_at", 0.0) or 0.0)
            if created_at >= float(not_before):
                continue
            path.unlink()
            removed += 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                path.rename(path.with_suffix(".invalid"))
            except OSError:
                pass
    stale_before = time.time() - 60.0
    for pattern in ("*.tmp", "*.invalid"):
        for path in directory.glob(pattern):
            try:
                if path.stat().st_mtime < stale_before:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


def finish_controller_command(request):
    if request is None:
        return
    try:
        Path(request["path"]).unlink()
    except (KeyError, OSError, TypeError):
        pass
