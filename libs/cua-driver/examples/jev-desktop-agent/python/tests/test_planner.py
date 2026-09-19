from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import DesktopOverview
from planner import OpenRouterPlanner, PassThroughPlanner


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


    def test_normalizer_removes_foreground_only_and_merges_final_verify(self):
        planner = OpenRouterPlanner(FakeClient())
        plan = planner._parse_plan(
            "search for Alan Turing",
            {
                "steps": [
                    {
                        "goal": "Bring the Mozilla Firefox window to the foreground",
                        "app": "Mozilla Firefox",
                        "text": None,
                        "completion": "Firefox is active",
                    },
                    {
                        "goal": "Open a new tab in Firefox",
                        "app": "Mozilla Firefox",
                        "text": None,
                        "completion": "A new tab is open",
                    },
                    {
                        "goal": "Run the search",
                        "app": "Mozilla Firefox",
                        "text": None,
                        "completion": "Search results are displayed",
                    },
                    {
                        "goal": "Confirm the search results are visible and stop",
                        "app": "Mozilla Firefox",
                        "text": None,
                        "completion": "Alan Turing search results are visible",
                    },
                ]
            },
        )
        self.assertEqual(
            [step.goal for step in plan.steps],
            ["Open a new tab in Firefox", "Run the search"],
        )
        self.assertEqual(
            plan.steps[-1].completion,
            "Alan Turing search results are visible",
        )


    def test_direct_mode_does_not_bind_to_mutable_firefox_tab_title(self):
        planner = PassThroughPlanner()
        plan = asyncio.run(
            planner.plan(
                "Open Firefox, open a new tab, search for Alan Turing",
                desktop=DesktopOverview(
                    windows=(
                        {
                            "app_name": "Jesse Model Analysis — Mozilla Firefox",
                            "title": "Jesse Model Analysis — Mozilla Firefox",
                        },
                    ),
                    apps=(),
                ),
            )
        )
        self.assertEqual(plan.steps[0].app, "Firefox")


    def test_direct_mode_infers_firefox_without_model(self):
        planner = PassThroughPlanner()
        plan = asyncio.run(
            planner.plan(
                "Open Firefox, search the web for Alan Turing",
                desktop=DesktopOverview(
                    windows=(
                        {
                            "app_name": "Mozilla Firefox",
                            "title": "Mozilla Firefox",
                        },
                    ),
                    apps=(),
                ),
            )
        )
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].app, "Firefox")
        self.assertEqual(
            plan.steps[0].goal,
            "Open Firefox, search the web for Alan Turing",
        )


if __name__ == "__main__":
    unittest.main()
