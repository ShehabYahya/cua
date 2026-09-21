"""Regression tests against the real AgentLoop and existing deterministic driver."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import loop as loop_module
from contracts import Candidate, Decision, DriverRefusal
from loop import AgentLoop
from telemetry import Telemetry, TelemetryConfig, identifier
from telemetry_agent import TelemetryAgentLoop, _ACTIVE, _install_shortlist_observer
from test_loop import FakeChooser, FakeDriver
from test_telemetry import drain


class InstrumentationTests(unittest.TestCase):
    def capture(self):
        return Telemetry(TelemetryConfig(mode="training", training_consent=True, queue_size=128))

    def test_same_decisions_actions_and_observation_count(self):
        baseline_driver, instrumented_driver = FakeDriver(), FakeDriver()
        baseline = asyncio.run(AgentLoop(baseline_driver, FakeChooser(), max_steps=4).run("open new tab", act=True))
        t = self.capture()
        result = asyncio.run(TelemetryAgentLoop(instrumented_driver, FakeChooser(), telemetry=t, max_steps=4).run("open new tab", act=True))
        self.assertEqual(result, baseline)
        self.assertEqual(instrumented_driver.executed, baseline_driver.executed)
        self.assertEqual(instrumented_driver.counter, baseline_driver.counter)
        events = drain(t)
        kinds = [e["event_kind"] for e in events]
        self.assertEqual(kinds.count("command_started"), 1)
        self.assertEqual(kinds.count("command_ended"), 1)
        self.assertEqual(kinds.count("observation"), 2)
        self.assertEqual(kinds.count("decision_requested"), 2)
        requests = [e for e in events if e["event_kind"] == "decision_requested"]
        self.assertEqual(len({e["decision_id"] for e in requests}), 2)
        self.assertEqual(len({e["command_id"] for e in events}), 1)
        for event in requests:
            data = event["data"]
            context = data["candidate_context"]
            self.assertGreaterEqual(data["full_pool_count"], data["shown_count"])
            self.assertEqual(context["shown_order"], list(context["alias_to_local_candidate"]))
            self.assertEqual(data["input_representation"], "sanitized_reconstruction")
        outcomes = [e["data"] for e in events if e["event_kind"] == "decision_outcome"]
        self.assertIs(outcomes[0]["observed_change"], True)
        self.assertEqual(outcomes[-1]["task_verified"], "unknown")
        self.assertIs(outcomes[-1]["observed_change"], None)

    def test_failed_projection_does_not_break_control(self):
        t, driver = self.capture(), FakeDriver()
        agent = TelemetryAgentLoop(driver, FakeChooser(), telemetry=t, max_steps=4)
        with patch.object(agent, "_decision_request", side_effect=RuntimeError("SECRET_PROVIDER_DATA")):
            result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(driver.executed, ["click-1"])
        self.assertGreater(t.status()["dropped_events"], 0)
        self.assertNotIn("SECRET_PROVIDER_DATA", json.dumps(drain(t)))

    def test_scoped_wrapper_idempotent_and_returns_original_identity(self):
        original = loop_module.shortlist_candidates
        sentinel = []
        try:
            loop_module.shortlist_candidates = lambda *a, **kw: sentinel
            _install_shortlist_observer()
            installed = loop_module.shortlist_candidates
            _install_shortlist_observer()
            self.assertIs(installed, loop_module.shortlist_candidates)
            self.assertIs(installed("goal", []), sentinel)
            self.assertIsNone(_ACTIVE.get())
        finally:
            loop_module.shortlist_candidates = original

    def test_concurrent_contexts_do_not_mix_commands(self):
        first, second = self.capture(), self.capture()
        async def scenario():
            return await asyncio.gather(
                TelemetryAgentLoop(FakeDriver(), FakeChooser(), telemetry=first).run("open new tab", act=True),
                TelemetryAgentLoop(FakeDriver(), FakeChooser(), telemetry=second).run("open new tab", act=True))
        results = asyncio.run(scenario())
        self.assertTrue(all(r.status == "completed" for r in results))
        left, right = drain(first), drain(second)
        self.assertEqual(sum(e["event_kind"] == "decision_requested" for e in left), 2)
        self.assertEqual(sum(e["event_kind"] == "decision_requested" for e in right), 2)
        self.assertTrue({e["command_id"] for e in left}.isdisjoint({e["command_id"] for e in right}))
        self.assertIsNone(_ACTIVE.get())

    def test_macro_partial_failure_preserves_each_child_and_unknown_delivery(self):
        class FailingDriver(FakeDriver):
            async def execute(self, candidate):
                self.executed.append(candidate.id)
                if len(self.executed) == 2:
                    raise RuntimeError("SECRET_TRANSPORT_FAILURE")
                return {"effect": "accepted"}
        t, driver = self.capture(), FailingDriver()
        agent = TelemetryAgentLoop(driver, FakeChooser(), telemetry=t)
        t.begin_command(identifier(), "macro")
        agent._decision_id, agent._action_id = identifier(), identifier()
        child = Candidate("repeated-id", "type local payload", "type_text", {"text": "SECRET_PAYLOAD"})
        macro = Candidate("macro", "two repeated children", None, {}, steps=(child, child, child))
        outcome, _, delivered = asyncio.run(agent._execute(macro, act=True, allow_foreground=False, cancel_event=None, result_ref={}))
        self.assertEqual((outcome, delivered), ("partial", 1))
        events = drain(t)
        dispatches = [e["data"] for e in events if e["event_kind"] == "action_dispatched"]
        results = [e["data"] for e in events if e["event_kind"] == "action_result"]
        self.assertEqual([d["child_index"] for d in dispatches], [0, 1])
        self.assertEqual([r["tool_accepted"] for r in results], [True, None])
        self.assertNotIn("SECRET", json.dumps(events))

    def test_foreground_retry_links_attempt_without_new_macro_child(self):
        class RetryDriver(FakeDriver):
            async def execute(self, candidate):
                self.executed.append(candidate.id)
                if len(self.executed) == 1:
                    raise DriverRefusal("click", "SECRET_REASON", recommended="foreground")
                return {}
        t, driver = self.capture(), RetryDriver()
        agent = TelemetryAgentLoop(driver, FakeChooser(), telemetry=t)
        agent._decision_id, agent._action_id = identifier(), identifier()
        child = Candidate("child", "click local control", "click", {})
        outcome, _, delivered = asyncio.run(agent._execute(child, act=True, allow_foreground=True, cancel_event=None, result_ref={}))
        self.assertEqual((outcome, delivered), ("executed", 1))
        rows = [e["data"] for e in drain(t) if e["event_kind"] == "action_dispatched"]
        self.assertEqual([row["child_index"] for row in rows], [0, 0])
        self.assertEqual(rows[1]["parent_attempt_id"], rows[0]["attempt_id"])
        self.assertTrue(rows[1]["foreground_retry"])

    def test_provider_failure_propagates_without_persisting_error(self):
        class BrokenChooser:
            async def choose(self, **kwargs):
                raise RuntimeError("SECRET_PROVIDER_RESPONSE")
        t = self.capture()
        agent = TelemetryAgentLoop(FakeDriver(), BrokenChooser(), telemetry=t)
        with self.assertRaisesRegex(RuntimeError, "SECRET_PROVIDER_RESPONSE"):
            asyncio.run(agent.run("open new tab", act=True))
        events = drain(t)
        self.assertNotIn("SECRET_PROVIDER_RESPONSE", json.dumps(events))
        self.assertEqual(events[-1]["data"]["status"], "failed")
        self.assertIsNone(_ACTIVE.get())

    def test_dry_run_never_dispatches(self):
        t, driver = self.capture(), FakeDriver()
        result = asyncio.run(TelemetryAgentLoop(driver, FakeChooser(), telemetry=t).run("open new tab", act=False))
        self.assertEqual(result.status, "dry_run")
        self.assertFalse(driver.executed)
        self.assertFalse(any(e["event_kind"] == "action_dispatched" for e in drain(t)))


if __name__ == "__main__":
    unittest.main()
