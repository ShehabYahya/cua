from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Candidate, Decision, Element, Observation
from loop import AgentLoop


class FakeDriver:
    def __init__(self) -> None:
        self.observations = 0
        self.executed: list[Candidate] = []

    async def observe(self, app=None):
        self.observations += 1
        return Observation(
            snapshot_id=f"s{self.observations}",
            pid=7,
            window_id=9,
            app="Demo",
            window_title="Demo",
            elements=(Element(1, f"s{self.observations}:1", "Button", "Next"),),
        )

    async def execute(self, candidate):
        self.executed.append(candidate)
        return {"effect": "confirmed"}


class ScriptedChooser:
    def __init__(self, choices):
        self.choices = iter(choices)

    async def choose(self, *, candidates, **kwargs):
        selected = next(self.choices)
        ids = {candidate.id for candidate in candidates}
        if selected not in ids:
            raise AssertionError(f"test selected missing candidate {selected}")
        return Decision(selected, 0.99, {selected: 0.99})


class LoopTest(unittest.TestCase):
    def test_reobserve_never_executes_and_next_action_uses_fresh_snapshot(self) -> None:
        driver = FakeDriver()
        chooser = ScriptedChooser(["reobserve", "click-1"])
        result = asyncio.run(
            AgentLoop(driver, chooser, max_steps=2).run("click next", act=True)
        )
        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual(driver.observations, 2)
        self.assertEqual(len(driver.executed), 1)
        self.assertEqual(driver.executed[0].snapshot_id, "s2")
        self.assertEqual(result.steps[0].outcome, "reobserve")
        self.assertEqual(result.steps[1].outcome, "executed")

    def test_dry_run_does_not_execute(self) -> None:
        driver = FakeDriver()
        chooser = ScriptedChooser(["click-1"])
        result = asyncio.run(AgentLoop(driver, chooser, max_steps=3).run("click next"))
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(driver.executed, [])


if __name__ == "__main__":
    unittest.main()
