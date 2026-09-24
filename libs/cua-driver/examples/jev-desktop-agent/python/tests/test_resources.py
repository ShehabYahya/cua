from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resources import DownloadTracker


class ResourceTest(unittest.TestCase):
    def test_detects_new_file_without_reading_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = DownloadTracker(tmp)
            before = tracker.snapshot()
            path = Path(tmp) / "report.pdf"
            path.write_bytes(b"pdf")
            changed = tracker.changed(before, tracker.snapshot())
            self.assertEqual([item.name for item in changed], ["report.pdf"])

    def test_detects_rename_by_inode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = DownloadTracker(tmp)
            old = Path(tmp) / "old.pdf"
            old.write_bytes(b"pdf")
            before = tracker.snapshot()
            new = Path(tmp) / "renamed.pdf"
            old.rename(new)
            changed = tracker.changed(before, tracker.snapshot())
            self.assertEqual([item.name for item in changed], ["renamed.pdf"])

    def test_tracks_nested_download_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = DownloadTracker(tmp)
            nested = Path(tmp) / "Telegram Desktop"
            nested.mkdir()
            path = nested / "report.pdf"
            path.write_bytes(b"pdf")
            snapshot = tracker.snapshot()
            self.assertIn(str(path.resolve()), snapshot)

    def test_ignores_partial_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = DownloadTracker(tmp)
            (Path(tmp) / "report.pdf.part").write_bytes(b"partial")
            self.assertEqual(tracker.snapshot(), {})

    def test_validate_recent_refuses_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            tracker = DownloadTracker(tmp)
            inside = Path(tmp) / "inside.pdf"
            outside = Path(other) / "outside.pdf"
            inside.write_bytes(b"1")
            outside.write_bytes(b"2")
            valid = tracker.validate_recent((str(outside), str(inside)))
            self.assertEqual(valid, (str(inside.resolve()),))


    def test_wait_for_changes_can_be_cancelled(self):
        import asyncio

        tracker = DownloadTracker(None)
        stop = __import__("threading").Event()
        stop.set()

        async def scenario():
            return await tracker.wait_for_changes(
                {},
                timeout=5.0,
                stop_event=stop,
            )

        self.assertEqual(asyncio.run(scenario()), ())

if __name__ == "__main__":
    unittest.main()
