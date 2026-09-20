from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Decision, DesktopOverview, Element, Observation
from loop import AgentLoop


GOAL = (
    'Open a new tab in Chrome, switch to Text Editor, '
    'and type "hello from Porter"'
)


class CompoundDriver:
    capture_bound_click = False
    coordinate_click_supported = False

    def __init__(self):
        self.new_tab = False
        self.typed = False
        self.counter = 0
        self.executed = []

    def _windows(self):
        return (
            {
                "app_name": "Chrome",
                "title": "New Tab" if self.new_tab else "Chrome",
                "pid": 7,
                "window_id": 9,
                "is_focused": True,
                "is_on_screen": True,
            },
            {
                "app_name": "Text Editor",
                "title": "Notes",
                "pid": 8,
                "window_id": 10,
                "is_focused": False,
                "is_on_screen": True,
            },
        )

    async def desktop_overview(
        self,
        *,
        include_screenshot=True,
        include_apps=True,
    ):
        return DesktopOverview(self._windows(), ())

    async def list_windows(self):
        return list(self._windows())

    async def list_apps(self):
        return []

    async def ensure_app(self, app):
        return None

    async def revive_session(self):
        return None

    async def observe(
        self,
        app=None,
        *,
        include_screenshot=True,
        windows=None,
        target_window=None,
    ):
        self.counter += 1
        snapshot = f"s{self.counter}"
        if target_window == (8, 10):
            return Observation(
                snapshot,
                8,
                10,
                "Text Editor",
                "Notes",
                (
                    Element(
                        1,
                        f"{snapshot}:1",
                        "entry",
                        "Document",
                        value="hello from Porter" if self.typed else None,
                    ),
                ),
            )
        if target_window != (7, 9):
            raise AssertionError(f"unexpected target {target_window}")
        return Observation(
            snapshot,
            7,
            9,
            "Chrome",
            "New Tab" if self.new_tab else "Chrome",
            (
                Element(
                    1,
                    f"{snapshot}:1",
                    "push button",
                    "New Tab",
                    actions=("click",),
                ),
            ),
        )

    async def execute(self, candidate):
        self.executed.append(candidate.id)
        if candidate.id == "hotkey-new-tab":
            self.new_tab = True
        elif candidate.id == "type-focused-text-1":
            self.typed = True
        else:
            raise AssertionError(f"unexpected action {candidate.id}")
        return {"effect": "confirmed"}

    def with_foreground(self, candidate):
        return candidate


class CompoundChooser:
    async def choose(self, *, observation, candidates, **kwargs):
        ids = {candidate.id for candidate in candidates}
        if observation.app == "Chrome" and not observation.window_title.startswith("New Tab"):
            selected = "hotkey-new-tab"
        elif observation.app == "Chrome":
            selected = "switch-window-8-10"
        elif any(element.value == "hello from Porter" for element in observation.elements):
            selected = "done"
        else:
            selected = "type-focused-text-1"

        if selected not in ids:
            raise AssertionError(f"{selected} missing from {sorted(ids)}")
        return Decision(selected, 0.99, {selected: 0.99})


class CompoundWorkflowTest(unittest.TestCase):
    def test_multi_app_goal_uses_explicit_session_switch_without_planner(self):
        driver = CompoundDriver()
        agent = AgentLoop(
            driver,
            CompoundChooser(),
            max_steps=8,
        )
        result = asyncio.run(agent.run(GOAL, act=True))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.completed_subgoals, 1)
        self.assertEqual(
            driver.executed,
            ["hotkey-new-tab", "type-focused-text-1"],
        )
        self.assertTrue(driver.typed)
        self.assertIn(
            "switch-window-8-10",
            [step.selected_id for step in result.steps],
        )
        self.assertEqual(len(result.plan.steps), 1)


if __name__ == "__main__":
    unittest.main()
