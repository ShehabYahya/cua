from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from confidence import assess_decision
from contracts import Candidate, Decision


class ConfidenceTest(unittest.TestCase):
    def candidates(self):
        return [
            Candidate("a", "Action A", "click", {}),
            Candidate("b", "Action B", "click", {}),
            Candidate("done", "Done", None, {}),
            Candidate("reobserve", "Reobserve", None, {}),
            Candidate("abstain", "Abstain", None, {}),
        ]

    def test_clear_winner_can_pass_below_absolute_threshold(self):
        decision = Decision(
            "a",
            0.53,
            {
                "a": 0.53,
                "b": 0.14,
                "done": 0.11,
                "reobserve": 0.10,
                "abstain": 0.12,
            },
        )
        assessment = assess_decision(
            decision,
            self.candidates(),
            min_confidence=0.55,
        )
        self.assertTrue(assessment.accepted)
        self.assertGreater(assessment.margin, 0.3)
        self.assertEqual(assessment.reason, "clear categorical winner")

    def test_ambiguous_choice_still_fails(self):
        decision = Decision(
            "a",
            0.43,
            {
                "a": 0.43,
                "b": 0.39,
                "done": 0.06,
                "reobserve": 0.06,
                "abstain": 0.06,
            },
        )
        assessment = assess_decision(
            decision,
            self.candidates(),
            min_confidence=0.55,
        )
        self.assertFalse(assessment.accepted)

    def test_safe_search_action_can_pass_with_clear_low_score_margin(self):
        decision = Decision(
            "a",
            0.37,
            {
                "a": 0.40,
                "b": 0.25,
                "done": 0.12,
                "reobserve": 0.11,
                "abstain": 0.12,
            },
        )
        assessment = assess_decision(
            decision,
            self.candidates(),
            min_confidence=0.55,
        )
        self.assertTrue(assessment.accepted)
        self.assertEqual(assessment.reason, "clear categorical winner")

    def test_consequential_action_needs_stronger_distribution(self):
        candidates = [
            Candidate(
                "send",
                'Activate button "Send".',
                "click",
                {},
                risk="confirm",
            ),
            Candidate("wait", "Wait", None, {}),
        ]
        decision = Decision(
            "send",
            0.40,
            {"send": 0.52, "wait": 0.30},
        )
        assessment = assess_decision(
            decision,
            candidates,
            min_confidence=0.55,
        )
        self.assertFalse(assessment.accepted)

    def test_terminal_choice_is_safe_to_verify_at_low_confidence(self):
        decision = Decision(
            "done",
            0.20,
            {"done": 0.20, "a": 0.19},
        )
        assessment = assess_decision(
            decision,
            self.candidates(),
            min_confidence=0.55,
        )
        self.assertTrue(assessment.accepted)
        self.assertEqual(
            assessment.reason,
            "non-mutating terminal choice",
        )


if __name__ == "__main__":
    unittest.main()
