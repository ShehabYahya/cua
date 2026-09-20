from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Decision, DesktopOverview, Element, Observation
from loop import AgentLoop


GOAL = (
    "Open Firefox, open a new tab, search the web for Alan Turing, "
    "and stop when the search results are visible"
)


class DirectSearchDriver:
    capture_bound_click = False
    coordinate_click_supported = False

    def __init__(self):
        self.new_tab = False
        self.typed = False
        self.results = False
        self.counter = 0
        self.executed = []
        self.revived = 0
        self.list_apps_calls = 0

    def _window(self):
        title = (
            "Alan Turing - Google Search — Mozilla Firefox"
            if self.results
            else "New Tab — Mozilla Firefox"
            if self.new_tab
            else "Mozilla Firefox"
        )
        return {
            "app_name": "Mozilla Firefox",
            "title": title,
            "pid": 7,
            "window_id": 9,
            "is_focused": True,
            "is_on_screen": True,
        }

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
        return DesktopOverview((self._window(),), ())

    async def list_windows(self):
        return [self._window()]

    async def list_apps(self):
        self.list_apps_calls += 1
        return []

    async def has_window(self, app):
        return "firefox" in app.casefold()

    async def ensure_app(self, app):
        return None

    async def revive_session(self):
        self.revived += 1

    async def observe(
        self,
        app=None,
        *,
        include_screenshot=True,
        windows=None,
        target_window=None,
    ):
        if include_screenshot:
            raise AssertionError("direct mode should not capture screenshots")
        if target_window != (7, 9):
            raise AssertionError(f"unexpected target: {target_window}")
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
    def test_search_runs_as_one_goal_with_one_jev_choice_per_iteration(self):
        driver = DirectSearchDriver()
        agent = AgentLoop(
            driver,
            DirectChooser(),
            max_steps=8,
        )
        result = asyncio.run(agent.run(GOAL, act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.plan.steps), 1)
        self.assertEqual(
            driver.executed,
            ["hotkey-new-tab", "type-focused-text-1", "press-enter"],
        )
        self.assertEqual(result.completed_subgoals, 1)
        self.assertEqual(driver.list_apps_calls, 0)
        # Initial observation + one resulting-state read for each real action.
        # The final done decision reuses the post-action state.
        self.assertEqual(driver.counter, 4)


if __name__ == "__main__":
    unittest.main()
