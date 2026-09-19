from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Candidate, Decision, Observation
from jev_adapter import HierarchicalChooser


class InnerChooser:
    def __init__(self):
        self.calls = []

    async def choose(self, *, candidates, **kwargs):
        self.calls.append([candidate.id for candidate in candidates])
        if candidates[0].id.startswith("group-"):
            return Decision("group-1", 0.9, {"group-1": 0.9})
        selected = next(candidate.id for candidate in candidates if candidate.id == "action-23")
        return Decision(selected, 0.8, {selected: 0.8})


class HierarchyTest(unittest.TestCase):
    def test_large_pool_uses_group_then_leaf(self):
        candidates = [
            Candidate(f"action-{index}", f"Action {index}", "click", {})
            for index in range(40)
        ]
        candidates += [
            Candidate("done", "Done", None, {}),
            Candidate("reobserve", "Reobserve", None, {}),
            Candidate("abstain", "Abstain", None, {}),
        ]
        inner = InnerChooser()
        chooser = HierarchicalChooser(inner, max_leaf_candidates=32, group_size=20)
        decision = asyncio.run(
            chooser.choose(
                goal="choose action 23",
                observation=Observation("s1", 7, 9, "Demo", "Demo", ()),
                candidates=candidates,
                history=[],
            )
        )
        self.assertEqual(decision.selected_id, "action-23")
        self.assertEqual(decision.confidence, 0.8)
        self.assertEqual(len(inner.calls), 2)
        self.assertIn("group-1", inner.calls[0])
        self.assertIn("action-23", inner.calls[1])


if __name__ == "__main__":
    unittest.main()
