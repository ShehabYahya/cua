from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Decision, Element, Observation, Plan, PlanStep, Verification
from loop import AgentLoop


class FakeDriver:
    capture_bound_click = False

    def __init__(self):
        self.counter = 0
        self.executed = []

    async def desktop_overview(self):
        from contracts import DesktopOverview
        return DesktopOverview((), ())

    async def has_window(self, app):
        return True

    async def ensure_app(self, app):
        return None

    async def observe(self, app=None):
        self.counter += 1
        label = "New Tab" if self.counter == 1 else "Search the web"
        return Observation(
            f"s{self.counter}",
            7,
            9,
            "Firefox",
            label,
            (
                Element(
                    1,
                    f"s{self.counter}:1",
                    "push button",
                    "New Tab",
                    actions=("click",),
                ),
            ),
        )

    async def execute(self, candidate):
        self.executed.append(candidate.id)
        return {"effect": "confirmed"}

    def with_foreground(self, candidate):
        return candidate


class FakePlanner:
    async def plan(self, goal, **kwargs):
        return Plan(
            goal,
            (PlanStep(goal, app="Firefox", completion="new tab exists"),),
        )

    async def repair_step(self, **kwargs):
        return kwargs["current"]


class FakeChooser:
    async def choose(self, *, candidates, history, **kwargs):
        selected = "click-1" if not history else "done"
        return Decision(selected, 0.99, {selected: 0.99})


class FakeVerifier:
    async def verify(self, *, observation, history, **kwargs):
        return Verification(
            bool(history and history[-1].executed),
            0.99,
            "changed",
        )


class LoopTest(unittest.TestCase):
    def test_execute_reobserve_verify_completes(self):
        driver = FakeDriver()
        agent = AgentLoop(
            driver,
            FakeChooser(),
            planner=FakePlanner(),
            verifier=FakeVerifier(),
            max_steps=4,
        )
        result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(driver.executed, ["click-1"])
        self.assertEqual(result.completed_subgoals, 1)

    def test_cancelled_before_execution_never_observes_or_executes(self):
        driver = FakeDriver()
        agent = AgentLoop(
            driver,
            FakeChooser(),
            planner=FakePlanner(),
            verifier=FakeVerifier(),
        )
        cancel = asyncio.Event()
        cancel.set()
        result = asyncio.run(
            agent.run(
                "open new tab",
                act=True,
                cancel_event=cancel,
            )
        )
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(driver.counter, 0)
        self.assertEqual(driver.executed, [])

    def test_dry_run_never_executes(self):
        driver = FakeDriver()
        agent = AgentLoop(
            driver,
            FakeChooser(),
            planner=FakePlanner(),
            verifier=FakeVerifier(),
        )
        result = asyncio.run(agent.run("open new tab", act=False))
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(driver.executed, [])


if __name__ == "__main__":
    unittest.main()
