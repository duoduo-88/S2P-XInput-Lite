"""Update checks for extension tools.

A tool ships a manifest next to its EXE: ``<EXE name>.s2p.json``::

    {"format": "s2p-extension", "name": "S2P-Joystick-Macro", "version": "0.1.0",
     "update_url": "https://.../s2p-extensions-updates.json"}

``update_url`` names an update feed: an HTTPS JSON document with the latest
version of one tool, or of several tools keyed by manifest name::

    {"format": "s2p-extension-updates",
     "tools": {"S2P-Joystick-Macro": {"version": "0.1.1", "url": "https://..."}}}

    {"format": "s2p-extension-updates", "version": "0.1.1", "url": "https://..."}

S2P only reads these documents when the user selects Check for Updates; it
never downloads or runs anything.  ``url`` is the page S2P offers to open.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from update_manager import parse_version

MANIFEST_SUFFIX = ".s2p.json"
MANIFEST_FORMAT = "s2p-extension"
FEED_FORMAT = "s2p-extension-updates"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_FEED_BYTES = 256 * 1024

UPDATE_AVAILABLE = "update_available"
UP_TO_DATE = "up_to_date"
NO_INFO = "no_info"          # no manifest, or the manifest names no update feed
CHECK_FAILED = "failed"      # the feed could not be read or did not list the tool


class ExtensionUpdateError(RuntimeError):
    """A manifest or update feed is missing, unreadable or invalid."""


@dataclass(frozen=True)
class ToolManifest:
    name: str
    version: str
    update_url: str = ""
    homepage: str = ""


@dataclass(frozen=True)
class UpdateResult:
    state: str
    current: str = ""
    latest: str = ""
    url: str = ""
    detail: str = ""
    path: str = ""  # the tool path this result belongs to


def manifest_path(tool_path):
    """``C:\\Tools\\X.exe`` -> ``C:\\Tools\\X.s2p.json``."""
    path = Path(tool_path)
    return path.with_name(path.stem + MANIFEST_SUFFIX)


def _https_url(value):
    text = str(value or "").strip()
    parts = urllib.parse.urlsplit(text)
    if parts.scheme != "https" or not parts.netloc:
        return ""
    return text


def _normal_version(value):
    return ".".join(str(part) for part in parse_version(value))


def read_tool_manifest(tool_path):
    """Read and validate the manifest beside ``tool_path``."""
    path = manifest_path(tool_path)
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ExtensionUpdateError("manifest too large")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ExtensionUpdateError("no manifest") from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise ExtensionUpdateError(f"unreadable manifest: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT:
        raise ExtensionUpdateError("not an S2P extension manifest")
    name = str(data.get("name") or "").strip()
    if not name:
        raise ExtensionUpdateError("manifest has no name")
    try:
        version = _normal_version(data.get("version"))
    except ValueError as exc:
        raise ExtensionUpdateError("manifest has no valid version") from exc
    return ToolManifest(
        name=name,
        version=version,
        update_url=_https_url(data.get("update_url")),
        homepage=_https_url(data.get("homepage")),
    )


def fetch_update_feed(url, *, user_agent, opener=urllib.request.urlopen, timeout=8.0):
    """Download one update feed (HTTPS only, size limited)."""
    if not _https_url(url):
        raise ExtensionUpdateError("update feed must use https")
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": user_agent},
    )
    try:
        with opener(request, timeout=float(timeout)) as response:
            body = response.read(MAX_FEED_BYTES + 1)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise ExtensionUpdateError(f"could not read the update feed: {exc}") from exc
    if len(body) > MAX_FEED_BYTES:
        raise ExtensionUpdateError("update feed too large")
    try:
        feed = json.loads(body.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        raise ExtensionUpdateError("update feed is not valid JSON") from exc
    if not isinstance(feed, dict) or feed.get("format") != FEED_FORMAT:
        raise ExtensionUpdateError("not an S2P extension update feed")
    return feed


def latest_in_feed(feed, name):
    """Return ``(version, url)`` for ``name`` from a single- or multi-tool feed."""
    entry = feed
    tools = feed.get("tools")
    if isinstance(tools, dict):
        entry = tools.get(name)
        if not isinstance(entry, dict):
            raise ExtensionUpdateError(f"the update feed does not list {name}")
    try:
        version = _normal_version(entry.get("version"))
    except ValueError as exc:
        raise ExtensionUpdateError("the update feed has no valid version") from exc
    return version, _https_url(entry.get("url"))


def check_tool_updates(tools, *, user_agent, opener=urllib.request.urlopen, timeout=8.0):
    """Check ``{tool_id: tool_path}``; returns ``{tool_id: UpdateResult}``.

    Tools that share one feed URL are answered from a single download.
    """
    feeds = {}
    results = {}
    for tool_id, tool_path in tools.items():
        path = str(tool_path)
        try:
            manifest = read_tool_manifest(tool_path)
        except ExtensionUpdateError as exc:
            results[tool_id] = UpdateResult(NO_INFO, detail=str(exc), path=path)
            continue
        if not manifest.update_url:
            results[tool_id] = UpdateResult(
                NO_INFO, current=manifest.version, detail="no update_url", path=path,
            )
            continue
        try:
            if manifest.update_url not in feeds:
                try:
                    feeds[manifest.update_url] = fetch_update_feed(
                        manifest.update_url, user_agent=user_agent, opener=opener,
                        timeout=timeout,
                    )
                except ExtensionUpdateError as exc:
                    feeds[manifest.update_url] = exc
            feed = feeds[manifest.update_url]
            if isinstance(feed, ExtensionUpdateError):
                raise feed
            latest, url = latest_in_feed(feed, manifest.name)
        except ExtensionUpdateError as exc:
            results[tool_id] = UpdateResult(
                CHECK_FAILED, current=manifest.version, detail=str(exc), path=path,
            )
            continue
        newer = parse_version(latest) > parse_version(manifest.version)
        results[tool_id] = UpdateResult(
            UPDATE_AVAILABLE if newer else UP_TO_DATE,
            current=manifest.version,
            latest=latest,
            url=url or manifest.homepage,
            path=path,
        )
    return results
