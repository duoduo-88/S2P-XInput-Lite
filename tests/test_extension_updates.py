import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import extension_updates as updates  # noqa: E402

FEED_URL = "https://example.test/s2p-extensions-updates.json"


def write_manifest(folder, exe_name, **fields):
    exe = Path(folder) / exe_name
    exe.write_bytes(b"MZ")
    data = {"format": "s2p-extension", "name": exe.stem, "version": "0.1.0",
            "update_url": FEED_URL}
    data.update(fields)
    updates.manifest_path(exe).write_text(json.dumps(data), encoding="utf-8")
    return str(exe)


class FakeOpener:
    def __init__(self, documents):
        self.documents = documents
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        body = self.documents[request.full_url]
        if isinstance(body, Exception):
            raise body
        if not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        return io.BytesIO(body)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def test_manifest_sits_beside_the_exe(self):
        self.assertEqual(
            updates.manifest_path(r"C:\Tools\Macro\S2P-Joystick-Macro.exe"),
            Path(r"C:\Tools\Macro\S2P-Joystick-Macro.s2p.json"),
        )

    def test_reads_a_valid_manifest(self):
        exe = write_manifest(self.folder, "Tool.exe", version="v1.2.3",
                             homepage="https://example.test/tool")
        manifest = updates.read_tool_manifest(exe)
        self.assertEqual(
            (manifest.name, manifest.version, manifest.update_url, manifest.homepage),
            ("Tool", "1.2.3", FEED_URL, "https://example.test/tool"),
        )

    def test_rejects_wrong_format_and_bad_version(self):
        exe = write_manifest(self.folder, "Tool.exe", format="other")
        with self.assertRaises(updates.ExtensionUpdateError):
            updates.read_tool_manifest(exe)
        exe = write_manifest(self.folder, "Tool.exe", version="latest")
        with self.assertRaises(updates.ExtensionUpdateError):
            updates.read_tool_manifest(exe)

    def test_plain_http_update_url_is_ignored(self):
        exe = write_manifest(self.folder, "Tool.exe", update_url="http://example.test/feed.json")
        self.assertEqual(updates.read_tool_manifest(exe).update_url, "")


class CheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def check(self, tools, documents):
        opener = FakeOpener(documents)
        return updates.check_tool_updates(tools, user_agent="S2P-XInput-Lite/test",
                                          opener=opener), opener

    def test_multi_tool_feed_is_downloaded_once(self):
        macro = write_manifest(self.folder, "Macro.exe")
        other = write_manifest(self.folder, "Other.exe", version="0.3.0")
        feed = {"format": "s2p-extension-updates", "tools": {
            "Macro": {"version": "0.2.0", "url": "https://example.test/v0.2.0"},
            "Other": {"version": "0.3.0", "url": "https://example.test/v0.3.0"},
        }}
        results, opener = self.check({"m": macro, "o": other}, {FEED_URL: feed})
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.requests[0].get_header("User-agent"), "S2P-XInput-Lite/test")
        self.assertEqual(
            (results["m"].state, results["m"].current, results["m"].latest, results["m"].url),
            (updates.UPDATE_AVAILABLE, "0.1.0", "0.2.0", "https://example.test/v0.2.0"),
        )
        self.assertEqual(results["m"].path, macro)
        self.assertEqual(results["o"].state, updates.UP_TO_DATE)

    def test_single_tool_feed(self):
        tool = write_manifest(self.folder, "Tool.exe")
        feed = {"format": "s2p-extension-updates", "version": "0.1.0", "url": "https://x.test/"}
        results, _opener = self.check({"t": tool}, {FEED_URL: feed})
        self.assertEqual(results["t"].state, updates.UP_TO_DATE)

    def test_missing_manifest_and_missing_feed_entry(self):
        bare = self.folder / "Bare.exe"
        bare.write_bytes(b"MZ")
        tool = write_manifest(self.folder, "Tool.exe")
        feed = {"format": "s2p-extension-updates", "tools": {}}
        results, opener = self.check({"b": str(bare), "t": tool}, {FEED_URL: feed})
        self.assertEqual(results["b"].state, updates.NO_INFO)
        self.assertEqual(results["t"].state, updates.CHECK_FAILED)
        self.assertEqual(len(opener.requests), 1)  # no manifest: nothing fetched

    def test_unreachable_or_invalid_feed_fails_only_that_check(self):
        tool = write_manifest(self.folder, "Tool.exe")
        for document in (OSError("offline"), b"<html>", {"format": "other"},
                         b"x" * (updates.MAX_FEED_BYTES + 1)):
            results, _opener = self.check({"t": tool}, {FEED_URL: document})
            self.assertEqual(results["t"].state, updates.CHECK_FAILED, document)

    def test_broken_responses_and_unexpected_errors_are_reported_as_failed(self):
        import http.client

        tool = write_manifest(self.folder, "Tool.exe")
        for document in (http.client.IncompleteRead(b"{"), RuntimeError("surprise")):
            results, _opener = self.check({"t": tool}, {FEED_URL: document})
            self.assertEqual(results["t"].state, updates.CHECK_FAILED, document)

    def test_redirect_away_from_https_is_refused(self):
        import urllib.error
        import urllib.request

        handler = updates._HttpsOnlyRedirects()
        request = urllib.request.Request(FEED_URL)
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "Found", {}, "http://example.test/feed.json")
        allowed = handler.redirect_request(request, None, 302, "Found", {}, "https://cdn.example.test/f.json")
        self.assertEqual(allowed.full_url, "https://cdn.example.test/f.json")

        class PlainResponse(io.BytesIO):
            def geturl(self):
                return "http://example.test/feed.json"

        tool = write_manifest(self.folder, "Tool.exe")
        feed = json.dumps({"format": "s2p-extension-updates", "version": "0.2.0"}).encode()
        results = updates.check_tool_updates(
            {"t": tool}, user_agent="test", opener=lambda request, timeout: PlainResponse(feed),
        )
        self.assertEqual(results["t"].state, updates.CHECK_FAILED)

    def test_download_page_must_be_https(self):
        tool = write_manifest(self.folder, "Tool.exe", homepage="https://example.test/home")
        feed = {"format": "s2p-extension-updates", "version": "0.2.0", "url": "file:///C:/x"}
        results, _opener = self.check({"t": tool}, {FEED_URL: feed})
        self.assertEqual(results["t"].url, "https://example.test/home")


if __name__ == "__main__":
    unittest.main()
