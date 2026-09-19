from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Plan:
    goal: str
    subgoals: tuple[str, ...]


class Planner(Protocol):
    async def plan(self, goal: str) -> Plan: ...


class PassThroughPlanner:
    """Phase-1 planner: keep one user goal. Compound decomposition comes next."""

    async def plan(self, goal: str) -> Plan:
        return Plan(goal=goal, subgoals=(goal,))
