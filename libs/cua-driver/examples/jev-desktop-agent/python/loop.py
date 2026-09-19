from __future__ import annotations

from dataclasses import dataclass, replace

from candidates import build_candidates
from contracts import (
    ABSTAIN,
    DONE,
    REOBSERVE,
    Candidate,
    Chooser,
    ConfirmationCallback,
    DesktopDriver,
    DriverRefusal,
    Plan,
    PlanStep,
    StepRecord,
    validate_decision,
)
from perception import NoopPerceiver
from planner import PassThroughPlanner
from policy import classify_risk
from verifier import ConservativeVerifier, GoalVerifier, state_changed
from writer import (
    OpenRouterWriter,
    PreparedText,
    quoted_text_slots,
    redact_prepared_text,
)


@dataclass(frozen=True)
class RunResult:
    status: str
    steps: tuple[StepRecord, ...]
    message: str
    plan: Plan
    completed_subgoals: int = 0


class AgentLoop:
    def __init__(
        self,
        driver: DesktopDriver,
        chooser: Chooser,
        *,
        planner=None,
        verifier: GoalVerifier | None = None,
        perceiver=None,
        writer: OpenRouterWriter | None = None,
        max_steps: int = 30,
        max_candidates: int = 96,
        min_confidence: float = 0.55,
        max_repairs_per_subgoal: int = 2,
    ) -> None:
        self._driver = driver
        self._chooser = chooser
        self._planner = planner or PassThroughPlanner()
        self._verifier = verifier or ConservativeVerifier()
        self._perceiver = perceiver or NoopPerceiver()
        self._writer = writer
        self._max_steps = max_steps
        self._max_candidates = max_candidates
        self._min_confidence = min_confidence
        self._max_repairs = max_repairs_per_subgoal
        self._recent_context: list[str] = []

    @property
    def recent_context(self) -> tuple[str, ...]:
        return tuple(self._recent_context[-8:])

    def _remember(self, goal: str, result: RunResult) -> None:
        actions = [
            item.description
            for item in result.steps
            if item.executed
        ][-4:]
        summary = (
            f"Previous command {goal!r} ended as {result.status}. "
            f"Completed {result.completed_subgoals}/{len(result.plan.steps)} subgoals. "
            f"Recent actions: {actions}. Result: {result.message}"
        )
        self._recent_context.append(summary)
        self._recent_context[:] = self._recent_context[-8:]

    async def _prepared_texts(
        self,
        *,
        original_goal: str,
        step: PlanStep,
        observation,
    ) -> tuple[PreparedText, ...]:
        slots = list(quoted_text_slots(original_goal))
        if step.text and all(item.text != step.text for item in slots):
            slots.append(
                PreparedText(
                    f"text-{len(slots) + 1}",
                    step.text,
                    "planner",
                )
            )
        if slots or self._writer is None:
            return tuple(slots)
        return await self._writer.prepare(
            original_goal=original_goal,
            step=step,
            observation=observation,
        )

    async def _verification(
        self,
        *,
        original_goal: str,
        step: PlanStep,
        observation,
        history: list[StepRecord],
    ):
        return await self._verifier.verify(
            original_goal=original_goal,
            step=step,
            observation=observation,
            history=history,
        )

    async def run(
        self,
        goal: str,
        *,
        app: str | None = None,
        act: bool = False,
        approve_consequential: bool = False,
        allow_foreground: bool = False,
        confirm: ConfirmationCallback | None = None,
        cancel_event=None,
    ) -> RunResult:
        if cancel_event is not None and cancel_event.is_set():
            empty_plan = Plan(goal=goal, steps=())
            result = RunResult(
                "cancelled",
                (),
                "Cancelled before execution.",
                empty_plan,
                0,
            )
            self._remember(goal, result)
            return result

        desktop = await self._driver.desktop_overview()
        plan = await self._planner.plan(
            goal,
            desktop=desktop,
            recent_context=self.recent_context,
        )
        history: list[StepRecord] = []
        global_step = 0
        completed_subgoals = 0

        for subgoal_index, planned_step in enumerate(
            plan.steps,
            start=1,
        ):
            current = planned_step
            repairs = 0
            no_progress = 0
            reobserve_count = 0
            last_candidate_id: str | None = None
            repeat_count = 0
            prepared: tuple[PreparedText, ...] | None = None

            while global_step < self._max_steps:
                if cancel_event is not None and cancel_event.is_set():
                    result = RunResult(
                        "cancelled",
                        tuple(history),
                        "Cancelled by the user.",
                        plan,
                        completed_subgoals,
                    )
                    self._remember(goal, result)
                    return result

                target_app = app or current.app
                if (
                    target_app
                    and not await self._driver.has_window(target_app)
                ):
                    if not act:
                        result = RunResult(
                            "needs_launch",
                            tuple(history),
                            f"{target_app} is not open; acting mode would launch it.",
                            plan,
                            completed_subgoals,
                        )
                        self._remember(goal, result)
                        return result
                    await self._driver.ensure_app(target_app)

                observation = await self._driver.observe(target_app)
                observation = await self._perceiver.enrich(
                    current.goal,
                    observation,
                )
                if prepared is None:
                    prepared = await self._prepared_texts(
                        original_goal=goal,
                        step=current,
                        observation=observation,
                    )

                candidates = build_candidates(
                    current.goal,
                    observation,
                    prepared_texts=prepared,
                    max_candidates=self._max_candidates,
                    allow_visual_clicks=self._driver.capture_bound_click,
                )
                decision_goal = redact_prepared_text(
                    current.goal,
                    prepared,
                )
                decision = await self._chooser.choose(
                    goal=decision_goal,
                    observation=observation,
                    candidates=candidates,
                    history=history,
                )
                candidate = validate_decision(
                    decision,
                    candidates,
                    current_snapshot_id=observation.snapshot_id,
                    current_capture_id=observation.capture_id,
                )
                global_step += 1

                if candidate.id == last_candidate_id:
                    repeat_count += 1
                else:
                    repeat_count = 1
                    last_candidate_id = candidate.id

                if decision.confidence < self._min_confidence:
                    history.append(
                        StepRecord(
                            global_step,
                            subgoal_index,
                            observation.snapshot_id,
                            candidate.id,
                            candidate.description,
                            decision.confidence,
                            False,
                            "low_confidence",
                            reason=(
                                "Jev confidence below policy threshold"
                            ),
                        )
                    )
                    if repairs < self._max_repairs:
                        current = await self._planner.repair_step(
                            original_goal=goal,
                            current=current,
                            observation=observation,
                            history=history,
                        )
                        repairs += 1
                        prepared = None
                        continue
                    result = RunResult(
                        "uncertain",
                        tuple(history),
                        candidate.description,
                        plan,
                        completed_subgoals,
                    )
                    self._remember(goal, result)
                    return result

                if candidate.id == DONE:
                    verification = await self._verification(
                        original_goal=goal,
                        step=current,
                        observation=observation,
                        history=history,
                    )
                    history.append(
                        StepRecord(
                            global_step,
                            subgoal_index,
                            observation.snapshot_id,
                            candidate.id,
                            candidate.description,
                            decision.confidence,
                            False,
                            (
                                "verified_done"
                                if verification.done
                                else "done_refuted"
                            ),
                            reason=verification.reason,
                        )
                    )
                    if verification.done:
                        completed_subgoals += 1
                        break
                    if repairs < self._max_repairs:
                        current = await self._planner.repair_step(
                            original_goal=goal,
                            current=current,
                            observation=observation,
                            history=history,
                        )
                        repairs += 1
                        prepared = None
                        continue
                    result = RunResult(
                        "uncertain",
                        tuple(history),
                        (
                            "Jev reported done but the completion "
                            "verifier could not confirm it."
                        ),
                        plan,
                        completed_subgoals,
                    )
                    self._remember(goal, result)
                    return result

                if candidate.id == REOBSERVE:
                    reobserve_count += 1
                    history.append(
                        StepRecord(
                            global_step,
                            subgoal_index,
                            observation.snapshot_id,
                            candidate.id,
                            candidate.description,
                            decision.confidence,
                            False,
                            "reobserve",
                        )
                    )
                    if (
                        reobserve_count >= 2
                        and repairs < self._max_repairs
                    ):
                        current = await self._planner.repair_step(
                            original_goal=goal,
                            current=current,
                            observation=observation,
                            history=history,
                        )
                        repairs += 1
                        reobserve_count = 0
                        prepared = None
                    continue

                if candidate.id == ABSTAIN:
                    history.append(
                        StepRecord(
                            global_step,
                            subgoal_index,
                            observation.snapshot_id,
                            candidate.id,
                            candidate.description,
                            decision.confidence,
                            False,
                            "abstained",
                        )
                    )
                    if repairs < self._max_repairs:
                        current = await self._planner.repair_step(
                            original_goal=goal,
                            current=current,
                            observation=observation,
                            history=history,
                        )
                        repairs += 1
                        prepared = None
                        continue
                    result = RunResult(
                        "abstained",
                        tuple(history),
                        candidate.description,
                        plan,
                        completed_subgoals,
                    )
                    self._remember(goal, result)
                    return result

                if candidate.risk == "safe":
                    contextual_risk = classify_risk(
                        f"{current.goal} {candidate.description}",
                        tool=candidate.tool,
                    )
                    if contextual_risk == "confirm":
                        candidate = replace(candidate, risk="confirm")

                if candidate.risk == "deny":
                    history.append(
                        StepRecord(
                            global_step,
                            subgoal_index,
                            observation.snapshot_id,
                            candidate.id,
                            candidate.description,
                            decision.confidence,
                            False,
                            "policy_denied",
                        )
                    )
                    result = RunResult(
                        "blocked",
                        tuple(history),
                        f"Policy denied: {candidate.description}",
                        plan,
                        completed_subgoals,
                    )
                    self._remember(goal, result)
                    return result

                if not act:
                    history.append(
                        StepRecord(
                            global_step,
                            subgoal_index,
                            observation.snapshot_id,
                            candidate.id,
                            candidate.description,
                            decision.confidence,
                            False,
                            "dry_run",
                        )
                    )
                    result = RunResult(
                        "dry_run",
                        tuple(history),
                        candidate.description,
                        plan,
                        completed_subgoals,
                    )
                    self._remember(goal, result)
                    return result

                if (
                    candidate.risk == "confirm"
                    and not approve_consequential
                ):
                    approved = (
                        await confirm(candidate)
                        if confirm is not None
                        else False
                    )
                    if not approved:
                        history.append(
                            StepRecord(
                                global_step,
                                subgoal_index,
                                observation.snapshot_id,
                                candidate.id,
                                candidate.description,
                                decision.confidence,
                                False,
                                "needs_confirmation",
                            )
                        )
                        result = RunResult(
                            "needs_confirmation",
                            tuple(history),
                            candidate.description,
                            plan,
                            completed_subgoals,
                        )
                        self._remember(goal, result)
                        return result

                if cancel_event is not None and cancel_event.is_set():
                    result = RunResult(
                        "cancelled",
                        tuple(history),
                        "Cancelled by the user before the next action.",
                        plan,
                        completed_subgoals,
                    )
                    self._remember(goal, result)
                    return result

                executed_candidate: Candidate = candidate
                try:
                    action_result = await self._driver.execute(
                        executed_candidate
                    )
                except DriverRefusal as refusal:
                    if (
                        refusal.recommended == "foreground"
                        and allow_foreground
                    ):
                        executed_candidate = (
                            self._driver.with_foreground(candidate)
                        )
                        action_result = await self._driver.execute(
                            executed_candidate
                        )
                    else:
                        history.append(
                            StepRecord(
                                global_step,
                                subgoal_index,
                                observation.snapshot_id,
                                candidate.id,
                                candidate.description,
                                decision.confidence,
                                False,
                                (
                                    "needs_foreground"
                                    if refusal.recommended == "foreground"
                                    else "refused"
                                ),
                                reason=refusal.reason,
                            )
                        )
                        result = RunResult(
                            (
                                "needs_foreground"
                                if refusal.recommended == "foreground"
                                else "refused"
                            ),
                            tuple(history),
                            str(refusal),
                            plan,
                            completed_subgoals,
                        )
                        self._remember(goal, result)
                        return result

                if cancel_event is not None and cancel_event.is_set():
                    history.append(
                        StepRecord(
                            global_step,
                            subgoal_index,
                            observation.snapshot_id,
                            candidate.id,
                            candidate.description,
                            decision.confidence,
                            True,
                            "cancelled_after_action",
                        )
                    )
                    result = RunResult(
                        "cancelled",
                        tuple(history),
                        "Cancelled by the user after the current action finished.",
                        plan,
                        completed_subgoals,
                    )
                    self._remember(goal, result)
                    return result

                effect = (
                    action_result.get("effect")
                    if isinstance(action_result, dict)
                    else None
                )
                after = await self._driver.observe(target_app)
                after = await self._perceiver.enrich(
                    current.goal,
                    after,
                )
                changed = state_changed(observation, after)
                history.append(
                    StepRecord(
                        global_step,
                        subgoal_index,
                        observation.snapshot_id,
                        candidate.id,
                        candidate.description,
                        decision.confidence,
                        True,
                        "executed",
                        effect=(
                            str(effect)
                            if effect is not None
                            else None
                        ),
                        reason=(
                            "state changed"
                            if changed
                            else "no semantic state change detected"
                        ),
                    )
                )

                verification = await self._verification(
                    original_goal=goal,
                    step=current,
                    observation=after,
                    history=history,
                )
                if verification.done:
                    completed_subgoals += 1
                    break

                no_progress = 0 if changed else no_progress + 1
                if no_progress >= 2 or repeat_count >= 3:
                    if repairs < self._max_repairs:
                        current = await self._planner.repair_step(
                            original_goal=goal,
                            current=current,
                            observation=after,
                            history=history,
                        )
                        repairs += 1
                        no_progress = 0
                        repeat_count = 0
                        prepared = None
                        continue
                    result = RunResult(
                        "stalled",
                        tuple(history),
                        (
                            verification.reason
                            or "no progress after repeated actions"
                        ),
                        plan,
                        completed_subgoals,
                    )
                    self._remember(goal, result)
                    return result
            else:
                result = RunResult(
                    "budget_exhausted",
                    tuple(history),
                    "step budget exhausted",
                    plan,
                    completed_subgoals,
                )
                self._remember(goal, result)
                return result

        result = RunResult(
            "completed",
            tuple(history),
            "All planned subgoals were independently verified.",
            plan,
            completed_subgoals,
        )
        self._remember(goal, result)
        return result
