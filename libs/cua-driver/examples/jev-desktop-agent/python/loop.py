from __future__ import annotations

from dataclasses import dataclass

from candidates import build_candidates
from contracts import ABSTAIN, REOBSERVE, Chooser, DesktopDriver, StepRecord, validate_decision
from writer import quoted_text_slots, redact_prepared_text


@dataclass(frozen=True)
class RunResult:
    status: str
    steps: tuple[StepRecord, ...]
    message: str


class AgentLoop:
    def __init__(
        self,
        driver: DesktopDriver,
        chooser: Chooser,
        *,
        max_steps: int = 24,
        max_candidates: int = 32,
        min_confidence: float = 0.55,
    ) -> None:
        self._driver = driver
        self._chooser = chooser
        self._max_steps = max_steps
        self._max_candidates = max_candidates
        self._min_confidence = min_confidence

    async def run(self, goal: str, *, app: str | None = None, act: bool = False) -> RunResult:
        history: list[StepRecord] = []
        prepared = quoted_text_slots(goal)
        decision_goal = redact_prepared_text(goal, prepared)

        for step in range(1, self._max_steps + 1):
            observation = await self._driver.observe(app)
            if observation.degraded or observation.truncated:
                return RunResult(
                    "needs_perception",
                    tuple(history),
                    (
                        "semantic observation is degraded or truncated; "
                        "visual fallback is not scaffolded yet"
                    ),
                )

            candidates = build_candidates(
                goal,
                observation,
                prepared_texts=prepared,
                max_candidates=self._max_candidates,
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
            )

            if decision.confidence < self._min_confidence:
                history.append(
                    StepRecord(
                        step,
                        observation.snapshot_id,
                        candidate.id,
                        decision.confidence,
                        False,
                        "low_confidence",
                    )
                )
                return RunResult("uncertain", tuple(history), candidate.description)

            if candidate.id == ABSTAIN:
                history.append(
                    StepRecord(
                        step,
                        observation.snapshot_id,
                        candidate.id,
                        decision.confidence,
                        False,
                        "abstained",
                    )
                )
                return RunResult("abstained", tuple(history), candidate.description)

            if candidate.id == REOBSERVE:
                history.append(
                    StepRecord(
                        step,
                        observation.snapshot_id,
                        candidate.id,
                        decision.confidence,
                        False,
                        "reobserve",
                    )
                )
                continue

            if not act:
                history.append(
                    StepRecord(
                        step,
                        observation.snapshot_id,
                        candidate.id,
                        decision.confidence,
                        False,
                        "dry_run",
                    )
                )
                return RunResult("dry_run", tuple(history), candidate.description)

            await self._driver.execute(candidate)
            history.append(
                StepRecord(
                    step,
                    observation.snapshot_id,
                    candidate.id,
                    decision.confidence,
                    True,
                    "executed",
                )
            )

        return RunResult("budget_exhausted", tuple(history), "step budget exhausted")
