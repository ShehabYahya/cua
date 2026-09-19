from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Candidate, Decision, Element, Observation, validate_decision


class ContractTest(unittest.TestCase):
    def test_compact_masks_field_value(self):
        element = Element(1, "s:1", "entry", "Message", value="private text")
        self.assertEqual(element.compact()["value"], "<set>")

    def test_stale_snapshot_rejected(self):
        candidate = Candidate("x", "do x", "click", {}, snapshot_id="old")
        with self.assertRaisesRegex(ValueError, "stale snapshot"):
            validate_decision(
                Decision("x", 0.9, {"x": 0.9}),
                [candidate],
                current_snapshot_id="new",
                current_capture_id=None,
            )

    def test_stale_capture_rejected(self):
        candidate = Candidate("x", "do x", "click", {}, capture_id="old")
        with self.assertRaisesRegex(ValueError, "stale capture"):
            validate_decision(
                Decision("x", 0.9, {"x": 0.9}),
                [candidate],
                current_snapshot_id="s1",
                current_capture_id="new",
            )


if __name__ == "__main__":
    unittest.main()
