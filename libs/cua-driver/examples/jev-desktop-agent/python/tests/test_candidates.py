from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from candidates import build_candidates
from contracts import Element, Observation
from writer import quoted_text_slots


class CandidateTest(unittest.TestCase):
    def observation(self) -> Observation:
        return Observation(
            snapshot_id="s1",
            pid=7,
            window_id=9,
            app="Browser",
            window_title="Example",
            elements=(
                Element(1, "s1:1", "Button", "Cancel"),
                Element(2, "s1:2", "Button", "Download report"),
                Element(3, "s1:3", "TextField", "Search"),
            ),
        )

    def test_goal_overlap_prioritizes_relevant_element_and_keeps_reserved(self) -> None:
        candidates = build_candidates(
            "download the report", self.observation(), max_candidates=4
        )
        self.assertEqual(candidates[0].id, "click-2")
        self.assertEqual(
            [candidate.id for candidate in candidates[-2:]],
            ["reobserve", "abstain"],
        )
        self.assertLessEqual(len(candidates), 4)

    def test_prepared_text_stays_in_local_arguments(self) -> None:
        slots = quoted_text_slots('search for "Alan Turing"')
        candidates = build_candidates(
            'search for "Alan Turing"',
            self.observation(),
            prepared_texts=slots,
        )
        typed = next(candidate for candidate in candidates if candidate.id.startswith("type-"))
        self.assertEqual(typed.arguments["text"], "Alan Turing")
        self.assertNotIn("Alan Turing", typed.description)

    def test_candidate_arguments_are_immutable(self) -> None:
        candidate = build_candidates("download", self.observation())[0]
        with self.assertRaises(TypeError):
            candidate.arguments["delivery_mode"] = "foreground"


if __name__ == "__main__":
    unittest.main()
