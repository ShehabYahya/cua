"""Optional, observational instrumentation for AgentLoop.

The shortlist function is wrapped once with a ContextVar-scoped observer: it still
returns the original object and only records for the currently instrumented run.
No global active command, alternate executor, extra observation, or model call.
The existing Jev input is recorded as a lossy reconstruction, NEVER exact replay.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import math
import threading
import time
from typing import Any

import loop as loop_module
from contracts import DriverRefusal
from telemetry import CHOICE_INSTRUCTIONS, FORMATTER, OUTCOMES, STATUSES, TOOLS, Telemetry, enum, flag, identifier, number

_ACTIVE: contextvars.ContextVar[Any] = contextvars.ContextVar("porter_telemetry_agent", default=None)
_INSTALL_LOCK = threading.Lock()


def _safe(telemetry: Telemetry, operation, *args, **kwargs):
    if not telemetry.enabled:
        return None
    try:
        return operation(*args, **kwargs)
    except Exception:
        telemetry.invalidate()
        return None


def _install_shortlist_observer() -> None:
    with _INSTALL_LOCK:
        original = loop_module.shortlist_candidates
        if getattr(original, "__porter_telemetry__", False):
            return

        @functools.wraps(original)
        def observed(*args, **kwargs):
            result = original(*args, **kwargs)
            agent = _ACTIVE.get()
            if agent is not None:
                pool = args[1] if len(args) > 1 else kwargs.get("candidates", ())
                _safe(agent.telemetry, agent._shortlisted, pool, result,
                      kwargs.get("page", 0), kwargs.get("limit"))
            return result

        observed.__porter_telemetry__ = True
        loop_module.shortlist_candidates = observed


class _DriverObserver:
    def __init__(self, driver: Any, agent: "TelemetryAgentLoop") -> None:
        self._inner = driver
        self._agent = agent
        self._foreground: set[int] = set()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def with_foreground(self, candidate):
        actual = self._inner.with_foreground(candidate)
        self._foreground.add(id(actual))
        return actual

    async def execute(self, candidate):
        agent, started = self._agent, time.monotonic()
        attempt = identifier()
        foreground = id(candidate) in self._foreground
        self._foreground.discard(id(candidate))
        _safe(agent.telemetry, agent._dispatch, candidate, attempt, foreground)
        try:
            result = await self._inner.execute(candidate)
        except BaseException as error:
            code = enum(getattr(error, "code", None), {"session_ended", "stale_snapshot", "stale_capture", "permission_denied"})
            _safe(agent.telemetry, agent._dispatch_result, attempt, False if isinstance(error, DriverRefusal) else None, code,
                  (time.monotonic() - started) * 1000)
            raise
        _safe(agent.telemetry, agent._dispatch_result, attempt, True, None,
              (time.monotonic() - started) * 1000)
        return result

    async def ensure_app(self, name):
        return await self._session_call("ensure_app", self._inner.ensure_app, name)

    async def revive_session(self):
        return await self._session_call("revive_session", self._inner.revive_session)

    async def list_apps(self):
        return await self._session_call("list_apps", self._inner.list_apps)

    async def _session_call(self, operation, function, *args):
        agent, started = self._agent, time.monotonic()
        attempt = identifier()
        _safe(agent.telemetry, agent._session_dispatch, operation, attempt)
        try:
            value = await function(*args)
        except BaseException:
            _safe(agent.telemetry, agent._dispatch_result, attempt, None, "unknown",
                  (time.monotonic() - started) * 1000)
            raise
        _safe(agent.telemetry, agent._dispatch_result, attempt, True, None,
              (time.monotonic() - started) * 1000)
        return value


class _ChooserObserver:
    def __init__(self, chooser, agent: "TelemetryAgentLoop") -> None:
        self._inner, self._agent = chooser, agent

    async def choose(self, *, goal, observation, candidates, history):
        agent = self._agent
        _safe(agent.telemetry, agent._flush_history, history)
        _safe(agent.telemetry, agent._decision_request, goal, observation, candidates, history)
        started = time.monotonic()
        try:
            decision = await self._inner.choose(goal=goal, observation=observation,
                                                candidates=candidates, history=history)
        except BaseException:
            _safe(agent.telemetry, agent.telemetry._record, "prediction_failed",
                  {"error_code": "provider_error", "latency_ms": (time.monotonic() - started) * 1000}, agent._decision_id)
            raise
        _safe(agent.telemetry, agent._prediction, decision, (time.monotonic() - started) * 1000)
        return decision


class TelemetryAgentLoop(loop_module.AgentLoop):
    def __init__(self, driver, chooser, *, telemetry: Telemetry, **kwargs) -> None:
        self.telemetry = telemetry
        self._pool_for_trace = []
        self._shown = ()
        self._suppressed = ()
        self._page = None
        self._decision_id = None
        self._action_id = None
        self._capture_id = None
        self._capture_at = None
        self._decision_number = 0
        self._history_count = 0
        self._pending_history_index = None
        self._request_map = {}
        self._last_attempt = None
        self._last_primitive = None
        self._child_index = -1
        self._build_ms = None
        self._slots = ()
        self._provider = "jev" if type(chooser).__name__ in {"TypeSafeChooser", "OpenRouterChooser"} else "unknown"
        super().__init__(_DriverObserver(driver, self), _ChooserObserver(chooser, self), **kwargs)
        _install_shortlist_observer()

    async def run(self, goal, **kwargs):
        self._decision_number = self._history_count = 0
        self._decision_id = self._pending_history_index = None
        self._action_id = self._capture_id = self._capture_at = None
        self._last_attempt = self._last_primitive = None
        self._suppressed = ()
        owned_command = self.telemetry.command_id is None
        if owned_command:
            _safe(self.telemetry, self.telemetry.begin_command, identifier(), goal, source="api")
        terminal = "failed"
        token = _ACTIVE.set(self)
        try:
            result = await super().run(goal, **kwargs)
            terminal = result.status
            return result
        except asyncio.CancelledError:
            terminal = "cancelled"
            raise
        finally:
            _ACTIVE.reset(token)
            if owned_command:
                _safe(self.telemetry, self.telemetry.end_command, terminal)
            self._pool_for_trace = []
            self._slots = ()
            self._request_map = {}

    def _build_pool(self, goal, observation, slots):
        started = time.monotonic()
        pool = super()._build_pool(goal, observation, slots)
        # Keep the very list that run() appends session candidates to. A separate
        # post-suppression list is passed into shortlist_candidates by the loop.
        self._pool_for_trace = pool
        self._slots = slots
        self._build_ms = (time.monotonic() - started) * 1000
        return pool

    def _session_candidates(self, **kwargs):
        self._suppressed = tuple(sorted(kwargs.get("suppressed", ())))
        return super()._session_candidates(**kwargs)

    def _shortlisted(self, pool, result, page, limit):
        self._shown = tuple(result)
        self._page = page if type(page) is int else None
        self._after_suppression_count = len(pool)
        self._shortlist_limit = limit if type(limit) is int else None

    async def _observe(self, *args, **kwargs):
        started = time.monotonic()
        observation = await super()._observe(*args, **kwargs)
        if not isinstance(observation, str):
            _safe(self.telemetry, self._observed, observation, (time.monotonic() - started) * 1000)
        return observation

    async def _read_state(self, **kwargs):
        prior = self._capture_id
        started = time.monotonic()
        result = await super()._read_state(**kwargs)
        if not isinstance(result, str) and self._capture_id == prior:
            # The base loop can return a desktop-only state without _observe().
            _safe(self.telemetry, self._observed, result[0], (time.monotonic() - started) * 1000)
        return result

    async def _inspect(self, *args, **kwargs):
        started = time.monotonic()
        result = await super()._inspect(*args, **kwargs)
        if result[0] is not None:
            _safe(self.telemetry, self._observed, result[0], (time.monotonic() - started) * 1000)
        return result

    def _observed(self, observation, elapsed):
        self._capture_id, self._capture_at = identifier(), time.monotonic()
        data = {"observation_id": self._capture_id, "latency_ms": elapsed}
        if self.telemetry.training:
            data["observation"] = self.telemetry.projection.observation(observation)
        self.telemetry._record("observation", data, self._decision_id)

    def _decision_request(self, goal, observation, candidates, history):
        self._decision_number += 1
        self._decision_id = identifier()
        self._action_id = identifier()
        self._pending_history_index = len(history)
        self._before_capture_id = self._capture_id
        self._child_index, self._last_primitive, self._last_attempt = -1, None, None
        self._request_map = {candidate.id: f"a{index}" for index, candidate in enumerate(candidates)}
        data = {"step_index": self._decision_number, "observation_id": self._capture_id,
                "observation_age_ms": (time.monotonic() - self._capture_at) * 1000 if self._capture_at else None,
                "full_pool_count": len(self._pool_for_trace), "shown_count": len(candidates),
                "page_index": self._page, "candidate_build_ms": self._build_ms,
                "provider": self._provider, "request_round_trip_ms": None}
        if self.telemetry.training:
            from telemetry import intent
            projection = self.telemetry.projection
            prepared_slots = projection.slots(self._slots)
            pool = [projection.candidate(c) for c in self._pool_for_trace[:512]]
            shown = [projection.candidate(c) for c in candidates[:32]]
            mapping = {f"a{i}": c["local_id"] for i, c in enumerate(shown)}
            shown_local = set(mapping.values())
            suppressed = [projection.aliases.get("c", value) for value in self._suppressed[:512]]
            state = {"goal_alias": projection.aliases.get("t", goal),
                     "constraints": intent(goal, self._slots),
                     "observation": projection.observation(observation),
                     # Jev's model-authored history is not copied into a training input.
                     "history": []}
            criteria = {f"a{i}": f"{c['tool']} {c['description']}" for i, c in enumerate(shown)}
            data.update(prepared_slots=prepared_slots, prepared_slots_omitted=max(0, len(self._slots) - 128), model_input={"state": state, "questions": {"driver_action": {"type": "choice", "instructions": CHOICE_INSTRUCTIONS, "criteria": criteria}}},
                        input_representation="sanitized_reconstruction", formatter_version=FORMATTER,
                        redaction_status="allowlisted", semantic_distinctions_reviewed=False,
                        capture_truncated=len(self._slots) > 128 or len(self._pool_for_trace) > 512 or len(candidates) > 32 or bool(state["observation"]["omitted_elements"]),
                        candidate_context={"full_pool": pool, "shown_order": list(mapping), "alias_to_local_candidate": mapping,
                                           "page_index": self._page, "after_suppression_count": getattr(self, "_after_suppression_count", None),
                                           "candidate_builder_revision": None,
                                           "suppressed": [{"local_id": value, "reason": "failed_route_same_state"} for value in suppressed],
                                           "not_shown": [{"local_id": c["local_id"], "reason": "suppressed" if c["local_id"] in suppressed else "shortlist_or_page"} for c in pool if c["local_id"] not in shown_local]},
                        model_context={"checkpoint_revision": None, "tokenizer_revision": None, "laya_code_revision": None,
                                       "device": None, "dtype": None, "quantization": None, "effective_temperature": None},
                        token_audit={"status": "unavailable", "option_markers_complete": None, "raw_logits": None},
                        provenance={"collection_mode": "training", "provider": self._provider,
                                    "jev_derived": True if self._provider == "jev" else None,
                                    "teacher_involved": None, "sampling_probability": 1.0,
                                    "training_eligible": False,
                                    "exclusion_reasons": ["not_exact_model_input", "missing_independent_label", "unreviewed_redaction", "provider_provenance_not_cleared"]})
        self.telemetry._record("decision_requested", data, self._decision_id)

    def _prediction(self, decision, elapsed):
        probabilities = {}
        for local_id, alias in self._request_map.items():
            value = number(getattr(decision, "probabilities", {}).get(local_id))
            if value is not None and 0 <= value <= 1:
                probabilities[alias] = value
        chosen = self._request_map.get(getattr(decision, "selected_id", None))
        values = sorted(probabilities.values(), reverse=True)
        data = {"latency_ms": elapsed, "provider": self._provider, "model_inference_ms": None,
                "confidence": number(getattr(decision, "confidence", None)),
                "confidence_definition": "provider_returned_unspecified", "option_count": len(self._request_map)}
        if self.telemetry.training:
            data.update(selected_id=chosen, probabilities=probabilities,
                        selected_probability=probabilities.get(chosen),
                        top_two_margin=values[0] - values[1] if len(values) >= 2 else None,
                        entropy_nats=-sum(p * math.log(p) for p in values if p > 0) if len(values) == len(self._request_map) else None,
                        probability_precision="provider_returned", raw_logits=None,
                        model_id_alias=self.telemetry.projection.aliases.get("m", getattr(decision, "model", None)))
        self.telemetry._record("prediction", data, self._decision_id)

    def _resolve(self, selected_id, candidates, observation):
        selected = super()._resolve(selected_id, candidates, observation)
        _safe(self.telemetry, self.telemetry._record, "selection_resolved",
              {"action_id": self._action_id, "binding_valid": selected is not None,
               "selected_id": self._request_map.get(selected_id) if self.telemetry.training else None}, self._decision_id)
        return selected

    async def _policy_gate(self, *args, **kwargs):
        result = await super()._policy_gate(*args, **kwargs)
        _safe(self.telemetry, self.telemetry._record, "policy_checked",
              {"denied": result[0] is not None, "approved": flag(result[1]), "action_id": self._action_id}, self._decision_id)
        return result

    def _dispatch(self, candidate, attempt, foreground):
        same = foreground and self._last_attempt is not None
        if not same:
            self._child_index += 1
        data = {"action_id": self._action_id, "attempt_id": attempt,
                "parent_attempt_id": self._last_attempt if same else None,
                "child_index": self._child_index, "foreground_retry": foreground,
                "observation_id": self._capture_id,
                "observation_age_ms": (time.monotonic() - self._capture_at) * 1000 if self._capture_at else None}
        if self.telemetry.training:
            data["actual_candidate"] = self.telemetry.projection.candidate(candidate)
        self._last_attempt, self._last_primitive = attempt, candidate.id
        self.telemetry._record("action_dispatched", data, self._decision_id)

    def _session_dispatch(self, operation, attempt):
        self.telemetry._record("session_action", {"operation": enum(operation, TOOLS),
                                                  "action_id": self._action_id, "attempt_id": attempt}, self._decision_id)

    def _dispatch_result(self, attempt, accepted, code, elapsed):
        self.telemetry._record("action_result", {"action_id": self._action_id, "attempt_id": attempt,
                                                  "tool_accepted": accepted, "error_code": code,
                                                  "step_verified": "unknown", "latency_ms": elapsed}, self._decision_id)

    def _flush_history(self, history):
        index = self._pending_history_index
        if index is None or len(history) <= index:
            return
        record = history[index]
        outcome = enum(getattr(record, "outcome", None), OUTCOMES)
        self.telemetry._record("decision_outcome", {"action_id": self._action_id, "outcome": outcome,
                                                    "executed": bool(getattr(record, "executed", False)),
                                                    "observed_change": True if outcome == "executed" else False if outcome == "executed_no_change" else None,
                                                    "step_verified": "unknown", "task_verified": "unknown",
                                                    "before_observation_id": self._before_capture_id,
                                                    "after_observation_id": self._capture_id,
                                                    "observation_reused": self._capture_id == self._before_capture_id}, self._decision_id)
        self._pending_history_index = None

    def _remember(self, goal, result):
        _safe(self.telemetry, self._flush_history, result.steps)
        return super()._remember(goal, result)
