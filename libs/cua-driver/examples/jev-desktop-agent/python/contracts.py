from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Protocol

REOBSERVE = "reobserve"
ABSTAIN = "abstain"
DONE = "done"


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


class DriverRefusal(RuntimeError):
    def __init__(
        self,
        tool: str,
        reason: str,
        *,
        code: str | None = None,
        recommended: str | None = None,
    ) -> None:
        super().__init__(f"{tool} refused: {reason}")
        self.tool = tool
        self.reason = reason
        self.code = code
        self.recommended = recommended


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height

    def compact(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class Element:
    index: int
    token: str | None
    role: str
    label: str
    enabled: bool = True
    value: str | None = None
    selected: bool | None = None
    actions: tuple[str, ...] = ()
    bounds: Rect | None = None
    source: str = "accessibility"
    browser_ref: str | None = None
    parent_index: int | None = None
    depth: int | None = None
    in_web_content: bool | None = None
    focused: bool | None = None
    visible: bool | None = None
    expanded: bool | None = None
    checked: bool | None = None

    def compact(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "index": self.index,
            "role": self.role,
            "label": self.label,
            "enabled": self.enabled,
            "source": self.source,
        }
        if self.value is not None:
            payload["value"] = "<set>" if self.value else ""
        if self.selected is not None:
            payload["selected"] = self.selected
        if self.parent_index is not None:
            payload["parent_index"] = self.parent_index
        if self.depth is not None:
            payload["depth"] = self.depth
        if self.in_web_content is not None:
            payload["in_web_content"] = self.in_web_content
        if self.focused is not None:
            payload["focused"] = self.focused
        if self.visible is not None:
            payload["visible"] = self.visible
        if self.expanded is not None:
            payload["expanded"] = self.expanded
        if self.checked is not None:
            payload["checked"] = self.checked
        if self.actions:
            payload["actions"] = list(self.actions[:8])
        if self.bounds:
            payload["bounds"] = self.bounds.compact()
        return payload


@dataclass(frozen=True)
class VisualRegion:
    id: str
    label: str
    kind: str
    bounds: Rect
    confidence: float
    interactive: bool = True
    source: str = "vision"

    def compact(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "bounds": self.bounds.compact(),
            "confidence": round(self.confidence, 3),
            "interactive": self.interactive,
            "source": self.source,
        }


@dataclass(frozen=True)
class Observation:
    snapshot_id: str
    pid: int
    window_id: int
    app: str
    window_title: str
    elements: tuple[Element, ...]
    visual_regions: tuple[VisualRegion, ...] = ()
    capture_id: str | None = None
    screenshot_path: str | None = None
    screenshot_width: int | None = None
    screenshot_height: int | None = None
    screenshot_error: str | None = None
    degraded: bool = False
    truncated: bool = False
    visual_summary: str | None = None
    browser_target_id: str | None = None
    browser_tab_id: str | None = None
    browser_url: str | None = None
    browser_title: str | None = None
    desktop_windows: tuple[Mapping[str, Any], ...] = ()
    recent_context: tuple[str, ...] = ()
    screenshot_frame_valid: bool | None = None

    def compact(self, *, limit: int = 96, visual_limit: int = 32) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "snapshot_id": self.snapshot_id,
            "app": self.app,
            "window": self.window_title,
            "degraded": self.degraded,
            "truncated": self.truncated,
            "elements": [element.compact() for element in self.elements[:limit]],
        }
        if self.visual_regions:
            payload["visual_regions"] = [
                region.compact() for region in self.visual_regions[:visual_limit]
            ]
        if self.visual_summary:
            payload["visual_summary"] = self.visual_summary[:1200]
        if self.capture_id:
            payload["capture_id"] = self.capture_id
        if self.browser_url or self.browser_title:
            payload["browser_page"] = {
                "url": self.browser_url,
                "title": self.browser_title,
            }
        if self.screenshot_error:
            payload["screenshot_error"] = self.screenshot_error[:300]
        if self.recent_context:
            payload["recent_context"] = list(self.recent_context[-8:])
        if self.desktop_windows:
            payload["desktop_windows"] = [
                {
                    "pid": window.get("pid"),
                    "window_id": window.get("window_id"),
                    "app_name": window.get("app_name"),
                    "title": window.get("title"),
                    "z_index": window.get("z_index"),
                    "is_on_screen": window.get("is_on_screen"),
                    "minimized": window.get("minimized"),
                }
                for window in self.desktop_windows[:12]
            ]
        return payload

    def fingerprint(self) -> str:
        material: dict[str, Any] = {
            # Window identity is part of the state so suppressions and paging
            # cannot leak between identically titled windows.
            "pid": self.pid,
            "window_id": self.window_id,
            "app": self.app,
            "window": self.window_title,
            "elements": [
                [
                    e.role,
                    e.label,
                    e.value,
                    e.selected,
                    e.enabled,
                    e.focused,
                    e.visible,
                    e.expanded,
                    e.checked,
                    e.parent_index,
                    e.depth,
                    e.in_web_content,
                ]
                for e in self.elements
            ],
            "visual": [[r.label, r.kind] for r in self.visual_regions[:80]],
            "browser_url": self.browser_url,
            "browser_title": self.browser_title,
        }
        if self.pid == 0:
            # Desktop selection has no window state of its own, so its entire
            # inventory is the observable state. Full inventory, deterministic
            # order, never truncated. snapshot_id/capture_id/recent_context are
            # deliberately excluded: they would register false changes on every
            # capture or context append.
            material["desktop_inventory"] = sorted(
                (
                    {
                        "pid": window.get("pid"),
                        "window_id": window.get("window_id"),
                        "app_name": window.get("app_name"),
                        "title": window.get("title"),
                        "is_on_screen": window.get("is_on_screen"),
                        "minimized": window.get("minimized"),
                        "z_index": window.get("z_index"),
                    }
                    for window in self.desktop_windows
                ),
                key=lambda item: (
                    item["pid"] if isinstance(item["pid"], int) else -1,
                    item["window_id"]
                    if isinstance(item["window_id"], int)
                    else -1,
                ),
            )
        raw = json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:20]


@dataclass(frozen=True)
class DesktopOverview:
    windows: tuple[Mapping[str, Any], ...]
    apps: tuple[Mapping[str, Any], ...]
    screenshot_path: str | None = None

    def compact(self, *, max_windows: int = 30, max_apps: int = 40) -> dict[str, Any]:
        windows: list[dict[str, Any]] = []
        for raw in self.windows[:max_windows]:
            windows.append(
                {
                    "app": raw.get("app_name") or raw.get("name"),
                    "title": raw.get("title"),
                    "pid": raw.get("pid"),
                    "on_screen": raw.get("is_on_screen"),
                    "z_index": raw.get("z_index"),
                }
            )
        apps: list[dict[str, Any]] = []
        for raw in self.apps[:max_apps]:
            apps.append(
                {
                    "name": raw.get("name"),
                    "running": raw.get("running"),
                    "bundle_id": raw.get("bundle_id"),
                }
            )
        return {"windows": windows, "apps": apps}


@dataclass(frozen=True)
class Candidate:
    id: str
    description: str
    tool: str | None
    arguments: Mapping[str, Any]
    snapshot_id: str | None = None
    capture_id: str | None = None
    source: str = "semantic"
    risk: str = "safe"
    steps: tuple[Candidate, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("candidate id must be a non-empty string")
        if not self.description:
            raise ValueError("candidate description must be non-empty")
        if self.risk not in {"safe", "confirm", "deny"}:
            raise ValueError("candidate risk must be safe, confirm, or deny")
        object.__setattr__(self, "arguments", _freeze(dict(self.arguments)))
        object.__setattr__(self, "steps", tuple(self.steps))


@dataclass(frozen=True)
class Decision:
    selected_id: str
    confidence: float
    probabilities: Mapping[str, float]
    model: str | None = None


@dataclass(frozen=True)
class PlanStep:
    goal: str
    app: str | None = None
    text: str | None = None
    completion: str | None = None


@dataclass(frozen=True)
class Plan:
    goal: str
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True)
class Verification:
    done: bool
    confidence: float
    reason: str


@dataclass(frozen=True)
class StepRecord:
    step: int
    subgoal: int
    snapshot_id: str
    selected_id: str
    description: str
    confidence: float
    executed: bool
    outcome: str
    effect: str | None = None
    reason: str | None = None

    def compact(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "subgoal": self.subgoal,
            "selected_id": self.selected_id,
            "description": self.description,
            "confidence": round(self.confidence, 3),
            "executed": self.executed,
            "outcome": self.outcome,
            "effect": self.effect,
            "reason": self.reason,
        }


ConfirmationCallback = Callable[[Candidate], Awaitable[bool]]


class DesktopDriver(Protocol):
    capture_bound_click: bool
    coordinate_click_supported: bool

    async def desktop_overview(
        self,
        *,
        include_screenshot: bool = True,
        include_apps: bool = True,
    ) -> DesktopOverview: ...

    async def has_window(self, app: str) -> bool: ...

    async def list_windows(self) -> list[dict[str, Any]]: ...

    async def list_apps(self) -> list[dict[str, Any]]: ...

    async def ensure_app(self, app: str) -> None: ...

    async def revive_session(self) -> None: ...

    async def observe(
        self,
        app: str | None = None,
        *,
        include_screenshot: bool = True,
        windows: tuple[Mapping[str, Any], ...] | None = None,
        target_window: tuple[int, int] | None = None,
    ) -> Observation: ...

    async def execute(self, candidate: Candidate) -> Mapping[str, Any]: ...

    def with_foreground(self, candidate: Candidate) -> Candidate: ...


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
    current_capture_id: str | None,
) -> Candidate:
    by_id = {candidate.id: candidate for candidate in candidates}
    if len(by_id) != len(candidates):
        raise ValueError("candidate table contains duplicate IDs")
    candidate = by_id.get(decision.selected_id)
    if candidate is None:
        raise ValueError(f"chooser selected unknown candidate: {decision.selected_id}")
    if candidate.snapshot_id is not None and candidate.snapshot_id != current_snapshot_id:
        raise ValueError("chooser selected an action bound to a stale snapshot")
    if candidate.capture_id is not None and candidate.capture_id != current_capture_id:
        raise ValueError("chooser selected an action bound to a stale capture")
    if not 0.0 <= decision.confidence <= 1.0:
        raise ValueError("chooser returned invalid confidence")
    return candidate
