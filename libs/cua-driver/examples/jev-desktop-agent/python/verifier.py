from __future__ import annotations

from typing import Protocol

from contracts import Observation, StepRecord


class GoalVerifier(Protocol):
    async def verify(
        self,
        *,
        goal: str,
        observation: Observation,
        history: list[StepRecord],
    ) -> bool | None: ...


class UnimplementedGoalVerifier:
    """Explicit Phase-1 placeholder; never claims task completion."""

    async def verify(
        self,
        *,
        goal: str,
        observation: Observation,
        history: list[StepRecord],
    ) -> bool | None:
        return None
