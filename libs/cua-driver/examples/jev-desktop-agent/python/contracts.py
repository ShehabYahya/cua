from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol

REOBSERVE = "reobserve"
ABSTAIN = "abstain"


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class Element:
    index: int
    token: str | None
    role: str
    label: str
    enabled: bool = True
    value: str | None = None

    def compact(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "index": self.index,
            "role": self.role,
            "label": self.label,
            "enabled": self.enabled,
        }
        if self.value is not None:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True)
class Observation:
    snapshot_id: str
    pid: int
    window_id: int
    app: str
    window_title: str
    elements: tuple[Element, ...]
    degraded: bool = False
    truncated: bool = False

    def compact(self, *, limit: int = 80) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "app": self.app,
            "window": self.window_title,
            "degraded": self.degraded,
            "truncated": self.truncated,
            "elements": [element.compact() for element in self.elements[:limit]],
        }


@dataclass(frozen=True)
class Candidate:
    id: str
    description: str
    tool: str | None
    arguments: Mapping[str, Any]
    snapshot_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("candidate id must be a non-empty string")
        if not self.description:
            raise ValueError("candidate description must be non-empty")
        object.__setattr__(self, "arguments", _freeze(dict(self.arguments)))


@dataclass(frozen=True)
class Decision:
    selected_id: str
    confidence: float
    probabilities: Mapping[str, float]
    model: str | None = None


@dataclass(frozen=True)
class StepRecord:
    step: int
    snapshot_id: str
    selected_id: str
    confidence: float
    executed: bool
    outcome: str


class DesktopDriver(Protocol):
    async def observe(self, app: str | None = None) -> Observation: ...

    async def execute(self, candidate: Candidate) -> Mapping[str, Any]: ...


class Chooser(Protocol):
    async def choose(
        self,
        *,
        goal: str,
        observation: Observation,
        candidates: list[Candidate],
        history: list[StepRecord],
    ) -> Decision: ...


def validate_decision(
    decision: Decision,
    candidates: list[Candidate],
    *,
    current_snapshot_id: str,
) -> Candidate:
    by_id = {candidate.id: candidate for candidate in candidates}
    if len(by_id) != len(candidates):
        raise ValueError("candidate table contains duplicate IDs")
    candidate = by_id.get(decision.selected_id)
    if candidate is None:
        raise ValueError(f"chooser selected unknown candidate: {decision.selected_id}")
    if candidate.snapshot_id is not None and candidate.snapshot_id != current_snapshot_id:
        raise ValueError("chooser selected an action bound to a stale snapshot")
    if not 0.0 <= decision.confidence <= 1.0:
        raise ValueError("chooser returned invalid confidence")
    return candidate
