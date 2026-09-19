from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import (
    Decision,
    DriverRefusal,
    Element,
    Observation,
    Plan,
    PlanStep,
    Verification,
)
from loop import AgentLoop


class FakeDriver:
    capture_bound_click = False

    def __init__(self):
        self.counter = 0
        self.executed = []

    async def desktop_overview(self, *, include_screenshot=True, include_apps=True):
        from contracts import DesktopOverview
        return DesktopOverview((), ())

    async def has_window(self, app):
        return True

    async def ensure_app(self, app):
        return None

    async def revive_session(self):
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
    def test_progress_reports_live_stages(self):
        driver = FakeDriver()
        messages = []
        agent = AgentLoop(
            driver,
            FakeChooser(),
            planner=FakePlanner(),
            verifier=FakeVerifier(),
            max_steps=4,
            progress=messages.append,
        )
        result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        joined = "\n".join(messages)
        self.assertIn("Reading desktop state", joined)
        self.assertIn("Planning task", joined)
        self.assertIn("Subgoal 1/1", joined)
        self.assertIn("asking Jev", joined)
        self.assertIn("Executing:", joined)
        self.assertIn("Verifier: done", joined)
        self.assertIn("All planned subgoals completed", joined)

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

    def test_sensitive_text_intent_is_blocked_on_generic_field(self):
        class SensitiveDriver(FakeDriver):
            async def observe(self, app=None):
                self.counter += 1
                return Observation(
                    f"s{self.counter}",
                    7,
                    9,
                    "Demo",
                    "Login",
                    (
                        Element(
                            1,
                            f"s{self.counter}:1",
                            "entry",
                            "Input",
                        ),
                    ),
                )

        class TypeChooser:
            async def choose(self, *, candidates, **kwargs):
                selected = next(
                    candidate.id
                    for candidate in candidates
                    if candidate.id.startswith("type-")
                )
                return Decision(selected, 0.99, {selected: 0.99})

        class SensitivePlanner:
            async def plan(self, goal, **kwargs):
                return Plan(
                    goal,
                    (
                        PlanStep(
                            'enter password "secret"',
                            app="Demo",
                            completion="password entered",
                        ),
                    ),
                )

            async def repair_step(self, **kwargs):
                return kwargs["current"]

        driver = SensitiveDriver()
        agent = AgentLoop(
            driver,
            TypeChooser(),
            planner=SensitivePlanner(),
            verifier=FakeVerifier(),
        )
        result = asyncio.run(
            agent.run(
                'enter password "secret"',
                act=True,
            )
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(driver.executed, [])

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

    def test_refuted_semantic_type_route_falls_back_to_focused_typing(self):
        class TypeDriver(FakeDriver):
            def __init__(self):
                super().__init__()
                self.typed = False

            async def observe(self, app=None):
                self.counter += 1
                return Observation(
                    f"s{self.counter}",
                    7,
                    9,
                    "Firefox",
                    "New Tab",
                    (
                        Element(
                            1,
                            f"s{self.counter}:1",
                            "combo box",
                            "Search with Google or enter address",
                            value="Alan Turing" if self.typed else None,
                        ),
                    ),
                )

            async def execute(self, candidate):
                self.executed.append(candidate.id)
                if candidate.id.startswith("type-focused-"):
                    self.typed = True
                return {"effect": "unverifiable"}

        class TypePlanner:
            async def plan(self, goal, **kwargs):
                return Plan(
                    goal,
                    (
                        PlanStep(
                            "Type the search query into the address bar",
                            app="Firefox",
                            text="Alan Turing",
                            completion="The address bar contains Alan Turing",
                        ),
                    ),
                )

            async def repair_step(self, **kwargs):
                return kwargs["current"]

        class TypeChooser:
            async def choose(self, *, candidates, history, **kwargs):
                ids = {candidate.id for candidate in candidates}
                semantic = next(
                    (
                        candidate.id
                        for candidate in candidates
                        if candidate.id.startswith("type-1-")
                    ),
                    None,
                )
                if not history and semantic:
                    selected = semantic
                else:
                    selected = "type-focused-text-1"
                    self_outer.assertIn(selected, ids)
                return Decision(selected, 0.80, {selected: 0.80})

        class TypeVerifier:
            def __init__(self, driver):
                self.driver = driver

            async def verify(self, **kwargs):
                return Verification(
                    self.driver.typed,
                    0.99 if self.driver.typed else 0.1,
                    "typed" if self.driver.typed else "not typed",
                )

        self_outer = self
        driver = TypeDriver()
        messages = []
        agent = AgentLoop(
            driver,
            TypeChooser(),
            planner=TypePlanner(),
            verifier=TypeVerifier(driver),
            max_steps=5,
            progress=messages.append,
        )
        result = asyncio.run(agent.run("search for Alan Turing", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(driver.executed), 2)
        self.assertTrue(driver.executed[0].startswith("type-1-"))
        self.assertEqual(driver.executed[1], "type-focused-text-1")
        self.assertIn(
            "Suppressing 1 route(s)",
            "\n".join(messages),
        )

    def test_session_ended_is_revived_and_reobserved(self):
        class SessionDriver(FakeDriver):
            def __init__(self):
                super().__init__()
                self.revived = 0
                self.failed_once = False

            async def execute(self, candidate):
                if not self.failed_once:
                    self.failed_once = True
                    raise DriverRefusal(
                        candidate.tool or "click",
                        "this session has ended",
                        code="session_ended",
                    )
                self.executed.append(candidate.id)
                return {"effect": "confirmed"}

            async def revive_session(self):
                self.revived += 1

        class RetryChooser:
            async def choose(self, *, candidates, history, **kwargs):
                if any(item.executed for item in history):
                    return Decision("done", 0.99, {"done": 0.99})
                return Decision("click-1", 0.99, {"click-1": 0.99})

        driver = SessionDriver()
        agent = AgentLoop(
            driver,
            RetryChooser(),
            planner=FakePlanner(),
            verifier=FakeVerifier(),
            max_steps=5,
        )
        result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(driver.revived, 1)
        self.assertEqual(driver.executed, ["click-1"])

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
