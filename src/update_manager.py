"""GitHub release checks and shared update-notification preferences."""

from __future__ import annotations

import configparser
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from config_utils import (
    CONFIG_PATH,
    atomic_write_config,
    config_file_lock,
    load_config,
)


GITHUB_OWNER = "duoduo-88"
GITHUB_REPOSITORY = "S2P-XInput-Lite"
GITHUB_RELEASES_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/releases"
)
LATEST_RELEASE_API_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/"
    f"{GITHUB_REPOSITORY}/releases/latest"
)
MAX_RELEASE_RESPONSE_BYTES = 2 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class UpdateCheckError(RuntimeError):
    """The latest GitHub release could not be queried or parsed."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    html_url: str
    notes: str = ""


def parse_version(value):
    """Return a comparable three-integer version tuple."""
    match = VERSION_PATTERN.fullmatch(str(value or "").strip())
    if match is None:
        raise ValueError(f"Unsupported version: {value!r}")
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate, current):
    """Return whether candidate is newer than the installed version."""
    return parse_version(candidate) > parse_version(current)


def _read_limited_response(response, maximum):
    chunks = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, maximum - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise UpdateCheckError("GitHub response exceeded the size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def check_latest_release(
    current_version,
    *,
    opener=urllib.request.urlopen,
    api_url=LATEST_RELEASE_API_URL,
    timeout=8.0,
):
    """Read GitHub's latest stable release without downloading executable data."""
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"S2P-XInput-Lite/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with opener(request, timeout=float(timeout)) as response:
            payload = json.loads(
                _read_limited_response(
                    response, MAX_RELEASE_RESPONSE_BYTES
                ).decode("utf-8")
            )
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        urllib.error.URLError,
    ) as exc:
        raise UpdateCheckError(f"Could not query the latest release: {exc}") from exc

    if not isinstance(payload, dict):
        raise UpdateCheckError("GitHub returned an invalid release document.")
    if payload.get("draft") or payload.get("prerelease"):
        raise UpdateCheckError("GitHub latest release was not a stable release.")

    tag_name = str(payload.get("tag_name") or "").strip()
    try:
        version_tuple = parse_version(tag_name)
    except ValueError as exc:
        raise UpdateCheckError(f"Invalid release tag: {tag_name!r}") from exc
    version = ".".join(str(part) for part in version_tuple)
    official_prefix = GITHUB_RELEASES_URL + "/tag/"
    return ReleaseInfo(
        version=version,
        tag_name=tag_name,
        html_url=f"{official_prefix}{tag_name}",
        notes=str(payload.get("body") or ""),
    )


def automatic_update_checks_enabled(path=CONFIG_PATH):
    try:
        return load_config(path).getboolean(
            "gui", "automatic_update_checks", fallback=True
        )
    except (OSError, ValueError, configparser.Error):
        return True


def ignored_update_version(path=CONFIG_PATH):
    try:
        return load_config(path).get(
            "gui", "ignored_update_version", fallback=""
        ).strip()
    except (OSError, configparser.Error):
        return ""


def save_update_preferences(
    *,
    automatic_checks=None,
    ignored_version=None,
    path=CONFIG_PATH,
):
    """Persist update preferences without replacing newer config changes."""
    with config_file_lock():
        config = load_config(path)
        if not config.has_section("gui"):
            config.add_section("gui")
        if automatic_checks is not None:
            config.set(
                "gui",
                "automatic_update_checks",
                "true" if automatic_checks else "false",
            )
        if ignored_version is not None:
            value = str(ignored_version or "").strip()
            if value:
                parse_version(value)
            config.set("gui", "ignored_update_version", value)
        atomic_write_config(config, path)
    return config
