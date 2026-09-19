from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Decision, DesktopOverview, Element, Observation
from loop import AgentLoop
from openrouter_client import OpenRouterClient
from planner import PassThroughPlanner
from verifier import OpenRouterVerifier
from writer import OpenRouterWriter


class NoModelClient:
    def chat_json(self, **kwargs):
        raise AssertionError("direct search flow should not need a chat-model call")


class DirectSearchDriver:
    capture_bound_click = False

    def __init__(self):
        self.new_tab = False
        self.typed = False
        self.results = False
        self.counter = 0
        self.executed = []
        self.revived = 0

    async def desktop_overview(
        self,
        *,
        include_screenshot=True,
        include_apps=True,
    ):
        if include_screenshot:
            raise AssertionError("direct mode should skip desktop screenshot")
        if include_apps:
            raise AssertionError("direct mode should skip full app inventory")
        return DesktopOverview(
            windows=(
                {
                    "app_name": "Mozilla Firefox",
                    "title": "Mozilla Firefox",
                    "pid": 7,
                    "window_id": 9,
                },
            ),
            apps=(),
        )

    async def has_window(self, app):
        return "firefox" in app.casefold()

    async def ensure_app(self, app):
        return None

    async def revive_session(self):
        self.revived += 1

    async def observe(self, app=None, *, include_screenshot=True):
        self.counter += 1
        snapshot = f"s{self.counter}"
        if self.results:
            return Observation(
                snapshot,
                7,
                9,
                "Mozilla Firefox",
                "Alan Turing - Google Search — Mozilla Firefox",
                (
                    Element(1, f"{snapshot}:1", "link", "Alan Turing - Wikipedia"),
                    Element(2, f"{snapshot}:2", "link", "Alan Turing | Britannica"),
                    Element(3, f"{snapshot}:3", "link", "Alan Turing biography"),
                ),
            )
        value = "Alan Turing" if self.typed else None
        return Observation(
            snapshot,
            7,
            9,
            "Mozilla Firefox",
            "New Tab — Mozilla Firefox" if self.new_tab else "Mozilla Firefox",
            (
                Element(
                    10,
                    f"{snapshot}:10",
                    "push button",
                    "New Tab",
                    actions=("click",),
                ),
                Element(
                    20,
                    f"{snapshot}:20",
                    "combo box",
                    "Search with Google or enter address",
                    value=value,
                ),
            ),
        )

    async def execute(self, candidate):
        self.executed.append(candidate.id)
        if candidate.id == "hotkey-new-tab":
            self.new_tab = True
        elif candidate.id == "type-focused-text-1":
            self.typed = True
        elif candidate.id == "press-enter":
            self.results = True
        else:
            raise AssertionError(f"unexpected direct action: {candidate.id}")
        return {"effect": "unverifiable"}

    def with_foreground(self, candidate):
        return candidate


class DirectChooser:
    async def choose(
        self,
        *,
        observation,
        candidates,
        **kwargs,
    ):
        ids = {candidate.id for candidate in candidates}
        if "Google Search" in observation.window_title:
            selected = "done"
        elif any(
            isinstance(element.value, str) and "Alan Turing" in element.value
            for element in observation.elements
        ):
            selected = "press-enter"
        elif observation.window_title.startswith("New Tab"):
            selected = "type-focused-text-1"
        else:
            selected = "hotkey-new-tab"
        if selected not in ids:
            raise AssertionError(f"{selected} missing from {sorted(ids)}")
        return Decision(selected, 0.90, {selected: 0.90})


class DirectSearchWorkflowTest(unittest.TestCase):
    def test_search_runs_as_one_goal_without_planner_or_intermediate_remote_verify(self):
        driver = DirectSearchDriver()
        no_model = NoModelClient()
        agent = AgentLoop(
            driver,
            DirectChooser(),
            planner=PassThroughPlanner(),
            verifier=OpenRouterVerifier(no_model),
            writer=OpenRouterWriter(no_model),
            max_steps=8,
        )
        goal = (
            "Open Firefox, open a new tab, search the web for Alan Turing, "
            "and stop when the search results are visible"
        )
        result = asyncio.run(agent.run(goal, act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.plan.steps), 1)
        self.assertEqual(
            driver.executed,
            ["hotkey-new-tab", "type-focused-text-1", "press-enter"],
        )
        self.assertEqual(result.completed_subgoals, 1)


if __name__ == "__main__":
    unittest.main()
