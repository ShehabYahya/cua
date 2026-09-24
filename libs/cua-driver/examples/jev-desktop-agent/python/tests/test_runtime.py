from __future__ import annotations

import asyncio
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import DesktopOverview, Plan, PlanStep
from events import RuntimeEvent, RuntimeEventBus
from loop import RunResult
from porter_voice import HandsFreeVoiceConfig
from runtime import PorterBusyError, PorterRuntime, PorterRuntimeConfig


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


class CancelledAgent(FakeAgent):
    async def run(self, goal, **kwargs):
        raise asyncio.CancelledError()


class BlockingAgent(FakeAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, goal, **kwargs):
        self.calls.append((goal, kwargs))
        self.started.set()
        await self.release.wait()
        cancel_event = kwargs.get("cancel_event")
        status = "cancelled" if cancel_event is not None and cancel_event.is_set() else "completed"
        return RunResult(
            status,
            (),
            "Command cancelled." if status == "cancelled" else "done",
            Plan(goal, (PlanStep(goal=goal, completion=goal),)),
            0 if status == "cancelled" else 1,
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

    def test_submit_rejects_instead_of_queueing(self):
        events = []
        agents = []

        def agent_factory(*args, **kwargs):
            agent = BlockingAgent(*args, **kwargs)
            agents.append(agent)
            return agent

        async def scenario():
            runtime = PorterRuntime(
                PorterRuntimeConfig(provider="typesafe", vision_enabled=False),
                event_sink=events.append,
                chooser_factory=lambda provider, model=None: FakeChooser(),
                driver_factory=FakeDriver,
                agent_factory=agent_factory,
            )
            await runtime.start()
            first = asyncio.create_task(runtime.submit("First"))
            await agents[0].started.wait()
            with self.assertRaises(PorterBusyError):
                await runtime.submit("Second")
            agents[0].release.set()
            await first
            self.assertEqual(len(agents[0].calls), 1)
            await runtime.stop()

        with patch.dict(
            "os.environ",
            {"TYPESAFE_API_KEY": "test", "OPENROUTER_API_KEY": ""},
            clear=False,
        ):
            asyncio.run(scenario())

    def test_cancelled_terminal_event_observes_cleared_runtime_state(self):
        snapshot = {}

        async def scenario():
            runtime = None

            def sink(event):
                if (
                    event.kind == "command_completed"
                    and event.data.get("status") == "cancelled"
                ):
                    snapshot["busy"] = runtime.busy
                    snapshot["cancelling"] = runtime.cancelling
                    snapshot["active_command_id"] = runtime.active_command_id

            agent = BlockingAgent()
            runtime = PorterRuntime(
                PorterRuntimeConfig(provider="typesafe", vision_enabled=False),
                event_sink=sink,
                chooser_factory=lambda provider, model=None: FakeChooser(),
                driver_factory=FakeDriver,
                agent_factory=lambda *args, **kwargs: agent,
            )
            await runtime.start()
            task = asyncio.create_task(runtime.submit("First"))
            await agent.started.wait()
            self.assertTrue(runtime.cancel())
            self.assertTrue(runtime.cancelling)
            self.assertFalse(runtime.cancel())
            agent.release.set()
            result = await task
            self.assertEqual(result.status, "cancelled")
            await runtime.stop()

        with patch.dict(
            "os.environ",
            {"TYPESAFE_API_KEY": "test", "OPENROUTER_API_KEY": ""},
            clear=False,
        ):
            asyncio.run(scenario())

        self.assertEqual(
            snapshot,
            {
                "busy": False,
                "cancelling": False,
                "active_command_id": None,
            },
        )

    def test_one_shot_voice_submits_exactly_once_and_terminates(self):
        events = []
        agents = []
        capture_calls = []

        class FakeCaptureEngine:
            def __init__(self, runtime, client, config, *, pause_event=None):
                capture_calls.append(("init", config))

            async def capture_once(self, *, stop_event, mode):
                capture_calls.append(("capture", mode))
                return SimpleNamespace(
                    utterance_id="utterance-1",
                    transcript="Open Firefox",
                )

        def agent_factory(*args, **kwargs):
            agent = FakeAgent(*args, **kwargs)
            agents.append(agent)
            return agent

        async def scenario():
            runtime = PorterRuntime(
                PorterRuntimeConfig(
                    provider="typesafe",
                    vision_enabled=False,
                ),
                event_sink=events.append,
                chooser_factory=lambda provider, model=None: FakeChooser(),
                driver_factory=FakeDriver,
                openrouter_factory=lambda: object(),
                agent_factory=agent_factory,
            )
            await runtime.start()
            try:
                with patch("runtime.VoiceCaptureEngine", FakeCaptureEngine):
                    transcript = await runtime.listen_once(
                        HandsFreeVoiceConfig(enabled=False)
                    )
                self.assertEqual(transcript, "Open Firefox")
                self.assertFalse(runtime.one_shot_voice_active)
                self.assertFalse(runtime.busy)
            finally:
                await runtime.stop()

        with patch.dict(
            "os.environ",
            {
                "TYPESAFE_API_KEY": "test",
                "OPENROUTER_API_KEY": "test",
            },
            clear=False,
        ):
            asyncio.run(scenario())

        self.assertEqual(
            [call for call in capture_calls if call[0] == "capture"],
            [("capture", "one_shot")],
        )
        self.assertEqual(len(agents), 1)
        self.assertEqual(
            [goal for goal, _ in agents[0].calls],
            ["Open Firefox"],
        )
        accepted = [
            event for event in events
            if event.kind == "command_started"
        ]
        self.assertEqual(len(accepted), 1)

    def test_one_shot_voice_can_be_cancelled_without_submit(self):
        events = []
        capture_started = asyncio.Event()
        agents = []

        class BlockingCaptureEngine:
            def __init__(self, runtime, client, config, *, pause_event=None):
                self.pause_event = pause_event

            async def capture_once(self, *, stop_event, mode):
                capture_started.set()
                while not stop_event.is_set():
                    await asyncio.sleep(0.01)
                return None

        def agent_factory(*args, **kwargs):
            agent = FakeAgent(*args, **kwargs)
            agents.append(agent)
            return agent

        async def scenario():
            runtime = PorterRuntime(
                PorterRuntimeConfig(
                    provider="typesafe",
                    vision_enabled=False,
                ),
                event_sink=events.append,
                chooser_factory=lambda provider, model=None: FakeChooser(),
                driver_factory=FakeDriver,
                openrouter_factory=lambda: object(),
                agent_factory=agent_factory,
            )
            await runtime.start()
            try:
                with patch("runtime.VoiceCaptureEngine", BlockingCaptureEngine):
                    task = asyncio.create_task(
                        runtime.listen_once(
                            HandsFreeVoiceConfig(enabled=False)
                        )
                    )
                    await capture_started.wait()
                    self.assertTrue(runtime.one_shot_voice_active)
                    with self.assertRaises(PorterBusyError):
                        await runtime.start_hands_free(
                            HandsFreeVoiceConfig(enabled=True)
                        )
                    self.assertTrue(await runtime.cancel_listen_once())
                    self.assertIsNone(await task)
                self.assertFalse(runtime.one_shot_voice_active)
            finally:
                await runtime.stop()

        with patch.dict(
            "os.environ",
            {
                "TYPESAFE_API_KEY": "test",
                "OPENROUTER_API_KEY": "test",
            },
            clear=False,
        ):
            asyncio.run(scenario())

        self.assertEqual(agents[0].calls, [])
        kinds = [event.kind for event in events]
        self.assertIn("voice_once_started", kinds)
        self.assertIn("voice_once_cancelled", kinds)

    def test_cancelled_coroutine_emits_terminal_cancelled_event(self):
        events = []

        async def scenario():
            runtime = PorterRuntime(
                PorterRuntimeConfig(
                    provider="typesafe",
                    vision_enabled=False,
                ),
                event_sink=events.append,
                chooser_factory=lambda provider, model=None: FakeChooser(),
                driver_factory=FakeDriver,
                agent_factory=CancelledAgent,
            )
            await runtime.start()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await runtime.submit("Open Firefox")
            finally:
                await runtime.stop()

        with patch.dict(
            "os.environ",
            {"TYPESAFE_API_KEY": "test", "OPENROUTER_API_KEY": ""},
            clear=False,
        ):
            asyncio.run(scenario())

        completed = [
            event for event in events if event.kind == "command_completed"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].data["status"], "cancelled")
        self.assertEqual(completed[0].message, "Command cancelled.")


if __name__ == "__main__":
    unittest.main()
