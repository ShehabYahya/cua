from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from porter_maintenance import latest_porter_release, version_key
except ModuleNotFoundError as error:
    if error.name and error.name.startswith("PySide6"):
        raise unittest.SkipTest(
            "PySide6 is an optional native-GUI dependency"
        ) from error
    raise


class PorterMaintenanceTest(unittest.TestCase):
    def test_version_key_orders_semver_like_tags(self):
        self.assertGreater(version_key("0.2.0"), version_key("0.1.9"))
        self.assertEqual(version_key("v1.2.3")[:3], (1, 2, 3))
        self.assertEqual(version_key("not-a-version"), (0, 0, 0, 0, ()))
        self.assertGreater(version_key("0.2.0"), version_key("0.2.0-rc.1"))
        self.assertGreater(
            version_key("0.2.0-rc.10"),
            version_key("0.2.0-rc.2"),
        )
        self.assertEqual(
            version_key("0.2.0+build.7"),
            version_key("0.2.0+build.8"),
        )

    def test_stable_release_wins_over_same_version_prerelease(self):
        release = latest_porter_release(
            [
                {
                    "tag_name": "porter-v0.2.0-rc.9",
                    "html_url": "https://example.test/rc",
                    "draft": False,
                },
                {
                    "tag_name": "porter-v0.2.0",
                    "html_url": "https://example.test/stable",
                    "draft": False,
                },
            ]
        )
        self.assertIsNotNone(release)
        self.assertEqual(release.tag, "porter-v0.2.0")

    def test_latest_release_ignores_unrelated_and_draft_releases(self):
        release = latest_porter_release(
            [
                {
                    "tag_name": "v9.0.0",
                    "html_url": "https://example.test/other",
                    "draft": False,
                },
                {
                    "tag_name": "porter-v0.3.0",
                    "html_url": "https://example.test/draft",
                    "draft": True,
                },
                {
                    "tag_name": "porter-v0.1.0",
                    "html_url": "https://example.test/one",
                    "draft": False,
                },
                {
                    "tag_name": "porter-v0.2.0",
                    "html_url": "https://example.test/two",
                    "draft": False,
                },
            ]
        )
        self.assertIsNotNone(release)
        self.assertEqual(release.version, "0.2.0")
        self.assertEqual(release.tag, "porter-v0.2.0")
        self.assertEqual(release.url, "https://example.test/two")

    def test_no_porter_release_returns_none(self):
        self.assertIsNone(
            latest_porter_release(
                [{"tag_name": "v1.0.0", "draft": False}]
            )
        )


if __name__ == "__main__":
    unittest.main()
