from __future__ import annotations

import asyncio
import math
from typing import Any

from contracts import Candidate, Decision, Observation, StepRecord


def _criteria(candidates: list[Candidate]) -> dict[str, str]:
    criteria = {candidate.id: candidate.description for candidate in candidates}
    if len(criteria) != len(candidates):
        raise ValueError("candidate table contains duplicate IDs")
    return criteria


def _history(history: list[StepRecord]) -> list[dict[str, Any]]:
    return [
        {
            "selected_id": item.selected_id,
            "executed": item.executed,
            "outcome": item.outcome,
        }
        for item in history[-8:]
    ]


class TypeSafeChooser:
    """Jev receives descriptions and IDs only; executable arguments stay local."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    async def choose(
        self,
        *,
        goal: str,
        observation: Observation,
        candidates: list[Candidate],
        history: list[StepRecord],
    ) -> Decision:
        return await asyncio.to_thread(
            self._choose_sync,
            goal,
            observation,
            candidates,
            history,
        )

    def _choose_sync(
        self,
        goal: str,
        observation: Observation,
        candidates: list[Candidate],
        history: list[StepRecord],
    ) -> Decision:
        from typesafe_sdk import Choice, TypeSafeClient

        criteria = _criteria(candidates)
        request = {
            "state": {
                "goal": goal,
                "observation": observation.compact(),
                "history": _history(history),
            },
            "questions": {
                "driver_action": Choice(
                    instructions=(
                        "Select exactly one supplied candidate ID for the next desktop step."
                    ),
                    criteria=criteria,
                )
            },
        }

        if self._client is not None:
            response = self._client.system_one(**request)
        else:
            with TypeSafeClient() as client:
                response = client.system_one(**request)

        answer = response.choices["driver_action"]
        if answer.choice not in criteria:
            raise ValueError(f"Jev selected unknown candidate: {answer.choice}")
        confidence = float(answer.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("Jev returned invalid confidence")

        probabilities: dict[str, float] = {}
        for candidate_id, raw in answer.probabilities.items():
            if candidate_id not in criteria:
                raise ValueError(f"Jev scored unknown candidate: {candidate_id}")
            value = float(raw)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("Jev returned invalid probability")
            probabilities[candidate_id] = value

        model = getattr(response, "model", None)
        return Decision(
            selected_id=str(answer.choice),
            confidence=confidence,
            probabilities=probabilities,
            model=model if isinstance(model, str) else None,
        )
