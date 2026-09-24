from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Decision, DesktopOverview, Element, Observation
from loop import AgentLoop


GOAL = (
    "Open Chrome, open a new tab, search the web for Alan Turing, "
    "and stop when the search results are visible"
)


class BundleDriver:
    capture_bound_click = False
    coordinate_click_supported = False

    def __init__(self):
        self.results = False
        self.counter = 0
        self.executed = []
        self.typed_text = None

    def _window(self):
        return {
            "app_name": "Chrome",
            "title": "Alan Turing - Search" if self.results else "Chrome",
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
        return DesktopOverview((self._window(),), ())

    async def list_windows(self):
        return [self._window()]

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
        if self.results:
            return Observation(
                snapshot,
                7,
                9,
                "Chrome",
                "Alan Turing - Search",
                (
                    Element(
                        100001,
                        None,
                        "link",
                        "Alan Turing - Wikipedia",
                        actions=("click",),
                        source="browser",
                        browser_ref="p2:1",
                    ),
                ),
                browser_target_id="bt-1",
                browser_tab_id="tab-1",
            )
        return Observation(
            snapshot,
            7,
            9,
            "Chrome",
            "Chrome",
            (
                Element(
                    1,
                    f"{snapshot}:1",
                    "combo box",
                    "Address and Search",
                ),
            ),
        )

    async def execute(self, candidate):
        self.executed.append(candidate.id)
        if candidate.id == "bundle-step-new-tab-type-text-1":
            self.typed_text = candidate.arguments["text"]
        if candidate.id == "bundle-step-new-tab-submit-text-1":
            self.results = True
        return {"effect": "confirmed"}

    def with_foreground(self, candidate):
        return candidate


class BundleChooser:
    def __init__(self):
        self.calls = 0

    async def choose(self, *, observation, candidates, **kwargs):
        self.calls += 1
        ids = {candidate.id for candidate in candidates}
        selected = (
            "done"
            if "Search" in observation.window_title
            else "bundle-browser-new-tab-search-text-1"
        )
        if selected not in ids:
            raise AssertionError(f"{selected} missing from {sorted(ids)}")
        return Decision(selected, 0.99, {selected: 0.99})


class CompoundWorkflowTest(unittest.TestCase):
    def test_compound_goal_executes_local_bundle_from_one_jev_choice(self):
        driver = BundleDriver()
        chooser = BundleChooser()
        agent = AgentLoop(
            driver,
            chooser,
            max_steps=4,
        )
        result = asyncio.run(agent.run(GOAL, app="Chrome", act=True))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.completed_subgoals, 1)
        self.assertEqual(chooser.calls, 2)
        self.assertEqual(driver.typed_text, "Alan Turing")
        self.assertEqual(
            driver.executed,
            [
                "bundle-step-new-tab-text-1",
                "bundle-step-new-tab-address-text-1",
                "bundle-step-new-tab-type-text-1",
                "bundle-step-new-tab-submit-text-1",
            ],
        )
        self.assertEqual(
            result.steps[0].selected_id,
            "bundle-browser-new-tab-search-text-1",
        )
        self.assertEqual(len(result.plan.steps), 1)


if __name__ == "__main__":
    unittest.main()
