from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from candidates import build_candidates
from confidence import assess_decision
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
    Verification,
    validate_decision,
)
from perception import NoopPerceiver
from planner import PassThroughPlanner
from policy import classify_risk, sensitive_text_intent
from resources import DownloadTracker
from verifier import (
    ConservativeVerifier,
    GoalVerifier,
    OpenRouterVerifier,
    local_verification,
    state_changed,
)
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
        download_root: str | None = None,
        progress: Callable[[str], None] | None = None,
        verify_every_action: bool = False,
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
        self._download_root = download_root
        self._download_tracker = DownloadTracker(download_root)
        self._recent_files: tuple[str, ...] = ()
        self._recent_context: list[str] = []
        self._progress_callback = progress
        self._verify_every_action = verify_every_action

    def _progress(self, message: str) -> None:
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(message)
        except Exception:
            pass

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
        if sensitive_text_intent(goal):
            plan = Plan(goal="<sensitive command>", steps=())
            result = RunResult(
                "blocked",
                (),
                (
                    "Credential-like text entry is blocked before planning "
                    "so the value is not sent to external models."
                ),
                plan,
                0,
            )
            self._recent_context.append(
                "A sensitive credential-entry command was blocked locally."
            )
            self._recent_context[:] = self._recent_context[-8:]
            return result

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

        direct_mode = isinstance(self._planner, PassThroughPlanner)
        self._progress("Reading desktop state...")
        desktop = await self._driver.desktop_overview(
            include_screenshot=not direct_mode,
            include_apps=not direct_mode,
        )
        self._progress(
            f"Desktop ready: {len(desktop.windows)} visible window(s), "
            f"{len(desktop.apps)} known app(s)."
        )
        if direct_mode:
            self._progress("Direct mode: one goal, no planner model call.")
        else:
            self._progress("Planning task...")
        plan = await self._planner.plan(
            goal,
            desktop=desktop,
            recent_context=self.recent_context,
        )
        if direct_mode:
            self._progress("Direct goal ready.")
        else:
            self._progress(f"Plan ready: {len(plan.steps)} subgoal(s).")
        history: list[StepRecord] = []
        global_step = 0
        completed_subgoals = 0

        for subgoal_index, planned_step in enumerate(
            plan.steps,
            start=1,
        ):
            current = planned_step
            if direct_mode:
                self._progress(f"Goal: {current.goal}")
            else:
                self._progress(
                    f"Subgoal {subgoal_index}/{len(plan.steps)}: {current.goal}"
                )
            repairs = 0
            no_progress = 0
            reobserve_count = 0
            low_confidence_count = 0
            force_visual_observation = False
            reobserved_fingerprints: set[str] = set()
            done_refuted_fingerprint: str | None = None
            refuted_action_signatures: set[tuple[str | None, str]] = set()
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
                    self._progress(f"Launching {target_app}...")
                    await self._driver.ensure_app(target_app)
                    self._progress(f"{target_app} is available.")

                include_screenshot = (
                    not direct_mode or force_visual_observation
                )
                self._progress(
                    f"Observing {target_app or 'current desktop window'}"
                    + (" with screenshot..." if include_screenshot else "...")
                )
                observation = await self._driver.observe(
                    target_app,
                    include_screenshot=include_screenshot,
                )
                force_visual_observation = False
                self._progress("Grounding current state...")
                observation = await self._perceiver.enrich(
                    current.goal,
                    observation,
                )
                self._progress(
                    f"Observed {observation.app} / {observation.window_title!r}: "
                    f"{len(observation.elements)} semantic element(s), "
                    f"{len(observation.visual_regions)} visual region(s)."
                )
                if prepared is None:
                    self._progress("Preparing any requested text...")
                    prepared = await self._prepared_texts(
                        original_goal=goal,
                        step=current,
                        observation=observation,
                    )

                observation_fingerprint = observation.fingerprint()
                candidates = build_candidates(
                    current.goal,
                    observation,
                    prepared_texts=prepared,
                    max_candidates=self._max_candidates,
                    allow_visual_clicks=self._driver.capture_bound_click,
                    download_root=self._download_root,
                    recent_files=self._download_tracker.validate_recent(
                        self._recent_files
                    ),
                )
                if (
                    done_refuted_fingerprint is not None
                    and done_refuted_fingerprint == observation_fingerprint
                ):
                    candidates = [
                        candidate
                        for candidate in candidates
                        if candidate.id != DONE
                    ]
                    self._progress(
                        "Completion was already refuted on this exact state; "
                        "temporarily suppressing the done candidate."
                    )
                if observation_fingerprint in reobserved_fingerprints:
                    before_count = len(candidates)
                    candidates = [
                        candidate
                        for candidate in candidates
                        if candidate.id != REOBSERVE
                    ]
                    if len(candidates) != before_count:
                        self._progress(
                            "Fresh observation is semantically unchanged; "
                            "suppressing reobserve so Jev must choose an action, "
                            "done, or abstain."
                        )
                if refuted_action_signatures:
                    before_count = len(candidates)
                    candidates = [
                        candidate
                        for candidate in candidates
                        if (
                            candidate.tool,
                            candidate.description,
                        )
                        not in refuted_action_signatures
                    ]
                    suppressed = before_count - len(candidates)
                    if suppressed:
                        self._progress(
                            f"Suppressing {suppressed} route(s) that were "
                            "already tried and refuted without changing state."
                        )
                self._progress(
                    f"Built {len(candidates)} candidate action(s); asking Jev..."
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
                assessment = assess_decision(
                    decision,
                    candidates,
                    min_confidence=self._min_confidence,
                )
                self._progress(
                    f"Jev selected {candidate.id} "
                    f"({decision.confidence:.0%}; "
                    f"p={assessment.selected_probability:.0%}, "
                    f"runner-up={assessment.runner_up_probability:.0%}, "
                    f"margin={assessment.margin:.0%}): "
                    f"{candidate.description}"
                )

                if candidate.id == last_candidate_id:
                    repeat_count += 1
                else:
                    repeat_count = 1
                    last_candidate_id = candidate.id

                if not assessment.accepted:
                    low_confidence_count += 1
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
                                f"{assessment.reason}; "
                                f"p={assessment.selected_probability:.3f}, "
                                f"runner_up={assessment.runner_up_probability:.3f}, "
                                f"margin={assessment.margin:.3f}"
                            ),
                        )
                    )
                    if low_confidence_count < 2:
                        if direct_mode:
                            force_visual_observation = True
                            self._progress(
                                "Decision is ambiguous; doing one richer "
                                "observation before deciding again."
                            )
                        else:
                            self._progress(
                                "Decision is ambiguous; re-observing once before "
                                "spending a planner repair call."
                            )
                        continue
                    if direct_mode:
                        result = RunResult(
                            "uncertain",
                            tuple(history),
                            (
                                "Direct-mode decision remained ambiguous after "
                                "one richer re-observation."
                            ),
                            plan,
                            completed_subgoals,
                        )
                        self._remember(goal, result)
                        return result
                    if repairs < self._max_repairs:
                        self._progress(
                            "Decision stayed ambiguous; asking planner to repair "
                            "the subgoal."
                        )
                        current = await self._planner.repair_step(
                            original_goal=goal,
                            current=current,
                            observation=observation,
                            history=history,
                        )
                        repairs += 1
                        low_confidence_count = 0
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

                low_confidence_count = 0

                if candidate.id == DONE:
                    self._progress("Jev says the subgoal is done; verifying independently...")
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
                    self._progress(
                        f"Verifier: {'done' if verification.done else 'not done'} "
                        f"({verification.confidence:.0%})"
                        + (f" — {verification.reason}" if verification.reason else "")
                    )
                    if verification.done:
                        completed_subgoals += 1
                        self._progress(
                            f"Subgoal {subgoal_index}/{len(plan.steps)} complete."
                        )
                        break
                    done_refuted_fingerprint = observation.fingerprint()
                    self._progress(
                        "Completion was refuted; keeping the same subgoal and "
                        "asking Jev for an actual action instead of replanning."
                    )
                    continue

                if candidate.id == REOBSERVE:
                    self._progress("Jev requested a fresh observation.")
                    reobserve_count += 1
                    reobserved_fingerprints.add(observation_fingerprint)
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
                    if direct_mode:
                        force_visual_observation = True
                        self._progress(
                            "Direct mode allows one reobserve for this state; "
                            "the next unchanged state will suppress reobserve."
                        )
                        continue
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
                    self._progress("Jev abstained from acting on the current state.")
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
                    if not direct_mode and repairs < self._max_repairs:
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
                        candidate.description,
                        tool=candidate.tool,
                    )
                    if (
                        candidate.tool in {"type_text", "browser_type"}
                        and sensitive_text_intent(current.goal)
                    ):
                        candidate = replace(candidate, risk="deny")
                    elif contextual_risk == "confirm":
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

                files_before = self._download_tracker.snapshot()
                executed_candidate: Candidate = candidate
                self._progress(f"Executing: {candidate.description}")
                try:
                    action_result = await self._driver.execute(
                        executed_candidate
                    )
                except DriverRefusal as refusal:
                    if refusal.code == "session_ended":
                        history.append(
                            StepRecord(
                                global_step,
                                subgoal_index,
                                observation.snapshot_id,
                                candidate.id,
                                candidate.description,
                                decision.confidence,
                                False,
                                "session_revived",
                                reason=refusal.reason,
                            )
                        )
                        self._progress(
                            "Driver lifecycle session ended; reviving it and "
                            "re-observing before retrying."
                        )
                        await self._driver.revive_session()
                        continue
                    if (
                        refusal.recommended == "foreground"
                        and allow_foreground
                    ):
                        executed_candidate = (
                            self._driver.with_foreground(candidate)
                        )
                        self._progress(
                            "Background delivery was unavailable; retrying the same "
                            "action with authorized foreground delivery..."
                        )
                        try:
                            action_result = await self._driver.execute(
                                executed_candidate
                            )
                        except DriverRefusal as foreground_refusal:
                            if foreground_refusal.code == "session_ended":
                                history.append(
                                    StepRecord(
                                        global_step,
                                        subgoal_index,
                                        observation.snapshot_id,
                                        candidate.id,
                                        candidate.description,
                                        decision.confidence,
                                        False,
                                        "session_revived",
                                        reason=foreground_refusal.reason,
                                    )
                                )
                                self._progress(
                                    "Foreground authorization ended the Driver "
                                    "lifecycle session; reviving it and "
                                    "re-observing before retrying."
                                )
                                await self._driver.revive_session()
                                continue
                            result = RunResult(
                                "refused",
                                tuple(history),
                                str(foreground_refusal),
                                plan,
                                completed_subgoals,
                            )
                            self._remember(goal, result)
                            return result
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

                self._progress("Action returned; checking resulting state...")
                file_wait = (
                    5.0
                    if (
                        candidate.tool == "browser_download"
                        or "download" in candidate.description.casefold()
                    )
                    else 0.0
                )
                file_changes = await self._download_tracker.wait_for_changes(
                    files_before,
                    timeout=file_wait,
                )
                if file_changes:
                    remembered = tuple(stamp.path for stamp in file_changes)
                    self._recent_files = self._download_tracker.validate_recent(
                        remembered + self._recent_files
                    )
                    file_names = ", ".join(
                        f'"{stamp.name}"'
                        for stamp in file_changes[:4]
                    )
                    self._recent_context.append(
                        "Local file resource changed in the approved "
                        f"download directory: {file_names}."
                    )
                    self._recent_context[:] = self._recent_context[-8:]

                effect = (
                    action_result.get("effect")
                    if isinstance(action_result, dict)
                    else None
                )
                self._progress("Re-observing after the action...")
                after = await self._driver.observe(target_app)
                after = await self._perceiver.enrich(
                    current.goal,
                    after,
                )
                changed = state_changed(observation, after)
                if changed:
                    done_refuted_fingerprint = None
                    refuted_action_signatures.clear()
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

                context_line = (
                    f"Worked in {after.app} / {after.window_title!r}: "
                    f"{candidate.description} "
                    f"Resulting page/window: "
                    f"{after.browser_title or after.window_title!r}."
                )
                self._recent_context.append(context_line)
                self._recent_context[:] = self._recent_context[-8:]

                self._progress(
                    "State changed." if changed else "No semantic state change detected."
                )
                verification = Verification(
                    False,
                    0.0,
                    "verification deferred until Jev signals done",
                )
                local = local_verification(
                    original_goal=goal,
                    step=current,
                    observation=after,
                )
                if local is not None:
                    verification = local
                elif self._verify_every_action:
                    self._progress("Verifying completion...")
                    verification = await self._verification(
                        original_goal=goal,
                        step=current,
                        observation=after,
                        history=history,
                    )
                elif not isinstance(self._verifier, OpenRouterVerifier):
                    verification = await self._verification(
                        original_goal=goal,
                        step=current,
                        observation=after,
                        history=history,
                    )

                if verification.confidence > 0.0 or verification.done:
                    self._progress(
                        f"Verifier: {'done' if verification.done else 'not done'} "
                        f"({verification.confidence:.0%})"
                        + (
                            f" — {verification.reason}"
                            if verification.reason
                            else ""
                        )
                    )
                else:
                    self._progress(
                        "Skipping remote verifier for this intermediate action."
                    )
                if verification.done:
                    completed_subgoals += 1
                    self._progress(
                        f"Subgoal {subgoal_index}/{len(plan.steps)} complete."
                    )
                    break

                if not changed and candidate.tool is not None:
                    refuted_action_signatures.add(
                        (candidate.tool, candidate.description)
                    )
                    escalation = (
                        action_result.get("escalation")
                        if isinstance(action_result, dict)
                        else None
                    )
                    recommended = None
                    if isinstance(escalation, dict):
                        recommended = (
                            escalation.get("recommended")
                            or escalation.get("target")
                        )
                    detail = (
                        f" Driver recommends {recommended}."
                        if recommended
                        else ""
                    )
                    self._progress(
                        "That route did not satisfy the verifier; trying an "
                        f"alternate route next.{detail}"
                    )

                no_progress = 0 if changed else no_progress + 1
                if no_progress >= 2 or repeat_count >= 3:
                    if direct_mode:
                        result = RunResult(
                            "stalled",
                            tuple(history),
                            (
                                verification.reason
                                or (
                                    "Direct mode made no progress after multiple "
                                    "distinct bounded attempts; stopping instead "
                                    "of entering a planner/retry loop."
                                )
                            ),
                            plan,
                            completed_subgoals,
                        )
                        self._remember(goal, result)
                        return result
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

        self._progress(
            "Goal completed." if direct_mode else "All planned subgoals completed."
        )
        result = RunResult(
            "completed",
            tuple(history),
            (
                "Goal independently verified."
                if direct_mode
                else "All planned subgoals were independently verified."
            ),
            plan,
            completed_subgoals,
        )
        self._remember(goal, result)
        return result
