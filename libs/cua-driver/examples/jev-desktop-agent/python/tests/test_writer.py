from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from writer import inferred_text_slots


class WriterTest(unittest.TestCase):
    def test_search_query_is_inferred_without_model(self):
        slots = inferred_text_slots(
            "Open Firefox, open a new tab, search the web for Alan Turing, "
            "and stop when the search results are visible"
        )
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].text, "Alan Turing")
        self.assertEqual(slots[0].source, "user-search")

    def test_quotes_win_over_heuristics(self):
        slots = inferred_text_slots(
            'search the web for "Alan Mathison Turing" and stop'
        )
        self.assertEqual(slots[0].text, "Alan Mathison Turing")


if __name__ == "__main__":
    unittest.main()
