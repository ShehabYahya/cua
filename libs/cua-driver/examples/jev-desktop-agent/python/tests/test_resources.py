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


if __name__ == "__main__":
    unittest.main()
