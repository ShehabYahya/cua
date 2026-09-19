from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planner import OpenRouterPlanner


class FakeClient:
    def chat_json(self, **kwargs):
        return {
            "steps": [
                {
                    "goal": "open Downloads",
                    "app": "Firefox",
                    "text": None,
                    "completion": "Downloads is open",
                },
                {
                    "goal": "search",
                    "app": "Firefox",
                    "text": "Alan Turing",
                    "completion": "results visible",
                },
            ]
        }


class PlannerTest(unittest.TestCase):
    def test_planner_builds_ordered_steps(self):
        planner = OpenRouterPlanner(FakeClient())
        plan = asyncio.run(planner.plan("do both"))
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[1].text, "Alan Turing")


if __name__ == "__main__":
    unittest.main()
