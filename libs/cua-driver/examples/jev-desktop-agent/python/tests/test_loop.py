from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Decision, DesktopOverview, DriverRefusal, Element, Observation
from loop import AgentLoop


class FakeDriver:
    capture_bound_click = False
    coordinate_click_supported = False

    def __init__(self):
        self.counter = 0
        self.executed = []
        self.clicked = False
        self.revived = 0

    def _window(self):
        return {
            "app_name": "Firefox",
            "title": "Search the web" if self.clicked else "New Tab",
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

    async def has_window(self, app):
        return True

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
        self.counter += 1
        return Observation(
            f"s{self.counter}",
            7,
            9,
            "Firefox",
            "Search the web" if self.clicked else "New Tab",
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
        self.clicked = True
        return {"effect": "confirmed"}

    def with_foreground(self, candidate):
        return candidate


class FakeChooser:
    async def choose(self, *, observation, candidates, **kwargs):
        selected = "done" if observation.window_title == "Search the web" else "click-1"
        ids = {candidate.id for candidate in candidates}
        if selected not in ids:
            raise AssertionError(f"{selected} missing from {sorted(ids)}")
        return Decision(selected, 0.99, {selected: 0.99})


class LoopTest(unittest.TestCase):
    def test_progress_reports_direct_loop_stages(self):
        driver = FakeDriver()
        messages = []
        agent = AgentLoop(
            driver,
            FakeChooser(),
            max_steps=4,
            progress=messages.append,
        )
        result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        joined = "\n".join(messages)
        self.assertIn("Reading desktop state", joined)
        self.assertIn("Desktop ready", joined)
        self.assertIn("Asking Jev", joined)
        self.assertIn("Jev selected click-1", joined)
        self.assertIn("Executing:", joined)
        self.assertNotIn("Planning task", joined)
        self.assertNotIn("Verifier:", joined)

    def test_resulting_state_is_reused_until_done(self):
        driver = FakeDriver()
        agent = AgentLoop(
            driver,
            FakeChooser(),
            max_steps=4,
        )
        result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(driver.executed, ["click-1"])
        self.assertEqual(result.completed_subgoals, 1)
        # Initial read + resulting-state read. The done choice does not trigger
        # an unnecessary extra observation.
        self.assertEqual(driver.counter, 2)

    def test_sensitive_text_intent_is_blocked_only_when_policy_enabled(self):
        class SensitiveDriver(FakeDriver):
            def _window(self):
                return {
                    "app_name": "Demo",
                    "title": "Login",
                    "pid": 7,
                    "window_id": 9,
                    "is_focused": True,
                    "is_on_screen": True,
                }

            async def observe(self, *args, **kwargs):
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

        class NeverChooser:
            async def choose(self, **kwargs):
                raise AssertionError("blocked goal should not reach Jev")

        driver = SensitiveDriver()
        agent = AgentLoop(
            driver,
            NeverChooser(),
            enforce_policy=True,
        )
        result = asyncio.run(
            agent.run(
                'enter password "secret"',
                act=True,
            )
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(driver.counter, 0)
        self.assertEqual(driver.executed, [])

    def test_cancelled_before_execution_never_observes_or_executes(self):
        driver = FakeDriver()
        agent = AgentLoop(driver, FakeChooser())
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

    def test_cancel_after_atomic_execute_skips_post_action_observation(self):
        cancel = asyncio.Event()

        class CancellingDriver(FakeDriver):
            async def execute(self, candidate):
                self.executed.append(candidate.id)
                self.clicked = True
                cancel.set()
                return {"effect": "confirmed"}

        driver = CancellingDriver()
        agent = AgentLoop(driver, FakeChooser())
        result = asyncio.run(
            agent.run(
                "open new tab",
                act=True,
                cancel_event=cancel,
            )
        )
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(driver.executed, ["click-1"])
        # Only the initial observation is allowed. The post-action state read is
        # fenced once cancellation becomes visible.
        self.assertEqual(driver.counter, 1)

    def test_cancel_wins_over_driver_session_revival(self):
        cancel = asyncio.Event()

        class EndingDriver(FakeDriver):
            async def execute(self, candidate):
                self.executed.append(candidate.id)
                cancel.set()
                raise DriverRefusal(
                    "session ended",
                    code="session_ended",
                )

        driver = EndingDriver()
        agent = AgentLoop(driver, FakeChooser())
        result = asyncio.run(
            agent.run(
                "open new tab",
                act=True,
                cancel_event=cancel,
            )
        )
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(driver.revived, 0)
        self.assertEqual(driver.counter, 1)

    def test_no_change_route_is_suppressed_then_focused_typing_succeeds(self):
        class TypeDriver(FakeDriver):
            def __init__(self):
                super().__init__()
                self.typed = False

            def _window(self):
                return {
                    "app_name": "Firefox",
                    "title": "New Tab",
                    "pid": 7,
                    "window_id": 9,
                    "is_focused": True,
                    "is_on_screen": True,
                }

            async def observe(self, *args, **kwargs):
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
                # Element-target typing intentionally produces no observable
                # change so the route becomes suppressed on this exact state.
                return {"effect": "unverifiable"}

        class TypeChooser:
            async def choose(self, *, observation, candidates, history, **kwargs):
                ids = {candidate.id for candidate in candidates}
                if any(
                    element.value == "Alan Turing"
                    for element in observation.elements
                ):
                    selected = "done"
                elif not history:
                    selected = next(
                        candidate.id
                        for candidate in candidates
                        if candidate.id.startswith("type-1-")
                    )
                else:
                    self_outer.assertFalse(
                        any(cid.startswith("type-1-") for cid in ids)
                    )
                    selected = "type-focused-text-1"
                self_outer.assertIn(selected, ids)
                return Decision(selected, 0.90, {selected: 0.90})

        self_outer = self
        driver = TypeDriver()
        messages = []
        agent = AgentLoop(
            driver,
            TypeChooser(),
            max_steps=5,
            progress=messages.append,
        )
        result = asyncio.run(agent.run("search for Alan Turing", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(driver.executed), 2)
        self.assertTrue(driver.executed[0].startswith("type-1-"))
        self.assertEqual(driver.executed[1], "type-focused-text-1")
        self.assertIn(
            "produced no observable change",
            "\n".join(messages),
        )

    def test_session_ended_is_revived_and_action_retried(self):
        class SessionDriver(FakeDriver):
            def __init__(self):
                super().__init__()
                self.failed_once = False

            async def execute(self, candidate):
                if not self.failed_once:
                    self.failed_once = True
                    raise DriverRefusal(
                        candidate.tool or "click",
                        "this session has ended",
                        code="session_ended",
                    )
                return await super().execute(candidate)

        driver = SessionDriver()
        agent = AgentLoop(
            driver,
            FakeChooser(),
            max_steps=5,
        )
        result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(driver.revived, 1)
        self.assertEqual(driver.executed, ["click-1"])
        self.assertEqual(
            [step.outcome for step in result.steps[:2]],
            ["session_revived", "executed"],
        )

    def test_tool_invocation_failure_revives_session_once(self):
        class InvocationDriver(FakeDriver):
            def __init__(self):
                super().__init__()
                self.failed_once = False

            async def execute(self, candidate):
                if not self.failed_once:
                    self.failed_once = True
                    raise DriverRefusal(
                        candidate.tool or "click",
                        "tool invocation failed",
                        code="tool_invocation_failed",
                    )
                return await super().execute(candidate)

        driver = InvocationDriver()
        agent = AgentLoop(
            driver,
            FakeChooser(),
            max_steps=5,
        )
        result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(driver.revived, 1)
        self.assertEqual(driver.executed, ["click-1"])
        self.assertEqual(
            [step.outcome for step in result.steps[:2]],
            ["session_revived", "executed"],
        )

    def test_reobserve_hesitation_is_bounded_on_unchanged_state(self):
        class StableDriver(FakeDriver):
            async def execute(self, candidate):
                self.executed.append(candidate.id)
                self.clicked = True
                return {"effect": "confirmed"}

        class ReobserveThenActChooser:
            async def choose(self, *, observation, candidates, **kwargs):
                ids = {candidate.id for candidate in candidates}
                if observation.window_title == "Search the web":
                    selected = "done"
                elif "reobserve" in ids:
                    selected = "reobserve"
                else:
                    selected = "click-1"
                return Decision(selected, 0.90, {selected: 0.90})

        driver = StableDriver()
        agent = AgentLoop(
            driver,
            ReobserveThenActChooser(),
            max_steps=6,
        )
        result = asyncio.run(agent.run("open a new tab", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(driver.executed, ["click-1"])
        self.assertEqual(
            [step.selected_id for step in result.steps[:3]],
            ["reobserve", "reobserve", "reobserve"],
        )
        self.assertEqual(result.steps[3].selected_id, "click-1")

    def test_dry_run_never_executes(self):
        driver = FakeDriver()
        agent = AgentLoop(driver, FakeChooser())
        result = asyncio.run(agent.run("open new tab", act=False))
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(driver.executed, [])


if __name__ == "__main__":
    unittest.main()
