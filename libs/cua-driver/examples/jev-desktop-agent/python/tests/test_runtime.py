from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import DesktopOverview, Plan, PlanStep
from events import RuntimeEvent, RuntimeEventBus
from loop import RunResult
from runtime import PorterRuntime, PorterRuntimeConfig


class FakeChooser:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeDriver:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True

    async def health_warnings(self):
        return ("demo warning",)

    def capability_limitations(self):
        return ("demo limitation",)

    def capability_summary(self):
        return {
            "native_observation": {"list_windows": True},
            "input": {"coordinate_click_supported": True},
        }

    async def desktop_overview(
        self,
        *,
        include_screenshot=True,
        include_apps=True,
    ):
        return DesktopOverview(
            windows=(
                {"pid": 7, "window_id": 9, "app_name": "Demo"},
            ),
            apps=(
                {"name": "Demo"},
                {"name": "Other"},
            ) if include_apps else (),
        )


class FakeAgent:
    def __init__(self, *args, progress=None, **kwargs) -> None:
        self.progress = progress
        self.calls = []

    async def run(self, goal, **kwargs):
        self.calls.append((goal, kwargs))
        if self.progress is not None:
            self.progress("Fake action started")
        return RunResult(
            "completed",
            (),
            "done",
            Plan(goal, (PlanStep(goal=goal, completion=goal),)),
            1,
        )


class RuntimeTest(unittest.TestCase):
    def test_event_bus_observer_failure_is_isolated(self):
        bus = RuntimeEventBus()
        seen = []

        def broken(event):
            raise RuntimeError("observer failed")

        bus.subscribe(broken)
        bus.subscribe(seen.append)
        bus.emit(RuntimeEvent("ready", "ok"))
        self.assertEqual([event.kind for event in seen], ["ready"])

    def test_diagnostics_use_the_resident_driver(self):
        chooser = FakeChooser()
        driver = FakeDriver()

        async def scenario():
            runtime = PorterRuntime(
                PorterRuntimeConfig(
                    provider="typesafe",
                    vision_enabled=False,
                    visual_click_mode="permissive",
                    enforce_policy=True,
                ),
                chooser_factory=lambda provider, model=None: chooser,
                driver_factory=lambda: driver,
                agent_factory=FakeAgent,
            )
            await runtime.start()
            payload = await runtime.diagnostics()
            await runtime.stop()
            return payload

        with patch.dict(
            "os.environ",
            {"TYPESAFE_API_KEY": "test", "OPENROUTER_API_KEY": ""},
            clear=False,
        ):
            payload = asyncio.run(scenario())

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["visible_windows"], 1)
        self.assertEqual(payload["known_apps"], 2)
        self.assertEqual(payload["visual_click_mode"], "permissive")
        self.assertTrue(payload["policy_enabled"])
        self.assertIn("demo warning", payload["warnings"])
        self.assertIn("demo limitation", payload["limitations"])
        self.assertTrue(
            payload["capabilities"]["native_observation"]["list_windows"]
        )

    def test_runtime_owns_backend_once_and_emits_command_events(self):
        events = []
        chooser = FakeChooser()
        driver = FakeDriver()
        agents = []

        def chooser_factory(provider, *, model=None):
            self.assertEqual(provider, "openrouter")
            self.assertIsNone(model)
            return chooser

        def driver_factory():
            return driver

        def agent_factory(*args, **kwargs):
            agent = FakeAgent(*args, **kwargs)
            agents.append(agent)
            return agent

        async def scenario():
            runtime = PorterRuntime(
                PorterRuntimeConfig(
                    provider="openrouter",
                    vision_enabled=False,
                    max_steps=7,
                    max_candidates=16,
                ),
                event_sink=events.append,
                chooser_factory=chooser_factory,
                driver_factory=driver_factory,
                agent_factory=agent_factory,
            )
            await runtime.start()
            self.assertTrue(runtime.started)
            result = await runtime.submit(
                "Open Firefox",
                act=True,
                allow_foreground=True,
            )
            self.assertEqual(result.status, "completed")
            self.assertFalse(runtime.busy)
            await runtime.stop()
            self.assertFalse(runtime.started)

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}, clear=False):
            asyncio.run(scenario())

        self.assertTrue(driver.entered)
        self.assertTrue(driver.exited)
        self.assertTrue(chooser.closed)
        self.assertEqual(len(agents), 1)
        self.assertEqual(len(agents[0].calls), 1)

        kinds = [event.kind for event in events]
        self.assertEqual(
            kinds,
            [
                "runtime_starting",
                "runtime_ready",
                "command_started",
                "agent_progress",
                "command_completed",
                "runtime_stopping",
                "runtime_stopped",
            ],
        )
        started = next(event for event in events if event.kind == "command_started")
        completed = next(
            event for event in events if event.kind == "command_completed"
        )
        self.assertIsNotNone(started.command_id)
        self.assertEqual(started.command_id, completed.command_id)


if __name__ == "__main__":
    unittest.main()
