"""Unify queued and legacy runtime-controller commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from command_queue import (
    COMMAND_QUEUE_DIR,
    cleanup_controller_commands,
    finish_controller_command,
    next_controller_command,
)


LEGACY_COMMAND_PATH = Path(__file__).with_name("controller_command.txt")


@dataclass
class PendingControllerCommand:
    command: str
    request_id: str
    _queued_request: dict | None
    _legacy_path: Path

    def clear_legacy(self):
        if self._queued_request is None:
            try:
                self._legacy_path.unlink(missing_ok=True)
            except OSError:
                pass

    def finish(self):
        finish_controller_command(self._queued_request)


class ControllerCommandInbox:
    def __init__(
        self,
        legacy_path=LEGACY_COMMAND_PATH,
        queue_dir=COMMAND_QUEUE_DIR,
    ):
        self.legacy_path = Path(legacy_path)
        self.queue_dir = Path(queue_dir)

    def reset(self, process_started_at):
        """Discard commands left by a previous connector process."""
        cleanup_controller_commands(process_started_at, self.queue_dir)
        try:
            self.legacy_path.unlink(missing_ok=True)
        except OSError:
            pass

    def next(self):
        queued_request = next_controller_command(self.queue_dir)
        if queued_request is not None:
            return PendingControllerCommand(
                command=queued_request["command"],
                request_id=queued_request["id"],
                _queued_request=queued_request,
                _legacy_path=self.legacy_path,
            )

        try:
            if not self.legacy_path.is_file():
                return None
            command = self.legacy_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None

        if not command:
            try:
                self.legacy_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        return PendingControllerCommand(
            command=command,
            request_id="",
            _queued_request=None,
            _legacy_path=self.legacy_path,
        )
