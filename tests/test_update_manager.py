import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import update_manager


class FakeResponse:
    def __init__(self, data):
        self.stream = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.stream.read(size)


def release_document(version="0.7.3"):
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "html_url": (
            "https://github.com/duoduo-88/S2P-XInput-Lite/"
            f"releases/tag/v{version}"
        ),
        "body": "release notes",
    }


class UpdateManagerTests(unittest.TestCase):
    def test_semantic_version_comparison_is_numeric(self):
        self.assertTrue(update_manager.is_newer_version("0.7.10", "0.7.2"))
        self.assertFalse(update_manager.is_newer_version("v0.7.2", "0.7.2"))
        with self.assertRaises(ValueError):
            update_manager.parse_version("0.7.2-beta")

    def test_latest_release_uses_the_official_api_and_release_page(self):
        opener = Mock(return_value=FakeResponse(
            json.dumps(release_document()).encode()
        ))

        release = update_manager.check_latest_release(
            "0.7.2", opener=opener
        )

        self.assertEqual(release.version, "0.7.3")
        self.assertEqual(release.tag_name, "v0.7.3")
        self.assertTrue(release.html_url.endswith("/releases/tag/v0.7.3"))
        request = opener.call_args.args[0]
        self.assertEqual(
            request.headers["X-github-api-version"],
            "2022-11-28",
        )

    def test_latest_release_replaces_an_unofficial_page_url(self):
        payload = release_document()
        payload["html_url"] = "https://example.invalid/update"
        opener = Mock(return_value=FakeResponse(json.dumps(payload).encode()))

        release = update_manager.check_latest_release(
            "0.7.2", opener=opener
        )

        self.assertEqual(
            release.html_url,
            "https://github.com/duoduo-88/S2P-XInput-Lite/"
            "releases/tag/v0.7.3",
        )

    def test_latest_release_rejects_a_nonsemantic_tag(self):
        payload = release_document()
        payload["tag_name"] = "latest"
        opener = Mock(return_value=FakeResponse(json.dumps(payload).encode()))

        with self.assertRaises(update_manager.UpdateCheckError):
            update_manager.check_latest_release("0.7.2", opener=opener)

    def test_update_preferences_preserve_unrelated_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text(
                "[gui]\nlanguage = en\n[custom]\nvalue = keep\n",
                encoding="utf-8",
            )

            update_manager.save_update_preferences(
                automatic_checks=False,
                ignored_version="0.7.3",
                path=path,
            )

            config = update_manager.load_config(path)
            self.assertFalse(config.getboolean("gui", "automatic_update_checks"))
            self.assertEqual(config.get("gui", "ignored_update_version"), "0.7.3")
            self.assertEqual(config.get("custom", "value"), "keep")

            path.write_text("[gui\nbroken", encoding="utf-8")
            self.assertTrue(
                update_manager.automatic_update_checks_enabled(path)
            )
            self.assertEqual(update_manager.ignored_update_version(path), "")


if __name__ == "__main__":
    unittest.main()
