from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from contracts import Observation, Rect, VisualRegion
from openrouter_client import DEFAULT_REASONING_MODEL, OpenRouterClient


def _iou(a: Rect, b: Rect) -> float:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x + a.width, b.x + b.width)
    y2 = min(a.y + a.height, b.y + b.height)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0


def _dedupe(
    observation: Observation,
    regions: list[VisualRegion],
) -> tuple[VisualRegion, ...]:
    kept: list[VisualRegion] = []
    # Screenshot-space regions are only compared with other screenshot-space
    # regions. Native/browser element bounds have no proven shared coordinate
    # space, so they are never used to suppress a visual region.
    visual_bounds = [region.bounds for region in observation.visual_regions]
    for region in regions:
        if any(
            bound and _iou(region.bounds, bound) >= 0.55
            for bound in visual_bounds
        ):
            continue
        kept.append(region)
    return tuple(kept)


class NoopPerceiver:
    async def enrich(self, goal: str, observation: Observation) -> Observation:
        return observation


class OpenRouterVisionPerceiver:
    def __init__(
        self,
        client: OpenRouterClient,
        *,
        model: str = DEFAULT_REASONING_MODEL,
        max_regions: int = 24,
    ) -> None:
        self.client = client
        self.model = model
        self.max_regions = max_regions

    async def enrich(self, goal: str, observation: Observation) -> Observation:
        import asyncio

        if not observation.screenshot_path:
            return observation
        path = Path(observation.screenshot_path)
        if not path.is_file():
            return observation

        # No lexical-overlap early exit: a menu word matching the goal is not
        # proof of grounding. If invoked with a real screenshot, make one
        # bounded vision request.
        try:
            regions, summary = await asyncio.to_thread(
                self._parse_sync,
                goal,
                observation,
            )
        except Exception:
            return observation

        new_regions = list(_dedupe(observation, regions))
        existing = list(observation.visual_regions)
        merged = list(existing)
        for region in new_regions:
            if any(
                _iou(region.bounds, current.bounds) >= 0.65
                for current in merged
            ):
                continue
            merged.append(region)

        return replace(
            observation,
            visual_regions=tuple(merged),
            visual_summary=summary or observation.visual_summary,
        )

    def _parse_sync(
        self,
        goal: str,
        observation: Observation,
    ) -> tuple[list[VisualRegion], str | None]:
        width = observation.screenshot_width or 0
        height = observation.screenshot_height or 0
        prompt = f"""Desktop task: {goal}
Window: {observation.app} — {observation.window_title}
Screenshot size: {width}x{height} pixels.

Identify only visible interactive controls relevant to the task that are NOT already obvious from this accessibility list:
{json.dumps([e.compact() for e in observation.elements[:80]], ensure_ascii=False)}

Return JSON only:
{{"summary":"short description of the visible state","regions":[{{"id":"v1","label":"New Tab","kind":"button","x":0,"y":0,"width":10,"height":10,"confidence":0.9}}]}}
Coordinates must be screenshot pixels with origin at the top-left. Return at most {self.max_regions} regions. Do not invent controls that are not visibly present.
"""
        body = self.client.chat_json(
            system="Ground visible computer controls in a screenshot. Return valid JSON only.",
            prompt=prompt,
            model=self.model,
            image_path=observation.screenshot_path,
            max_tokens=1600,
        )
        if not isinstance(body, dict):
            return [], None
        summary = body.get("summary") if isinstance(body.get("summary"), str) else None
        raw_regions = body.get("regions")
        if not isinstance(raw_regions, list):
            return [], summary
        regions: list[VisualRegion] = []
        for index, raw in enumerate(raw_regions[: self.max_regions]):
            if not isinstance(raw, dict):
                continue
            try:
                x = float(raw["x"])
                y = float(raw["y"])
                w = float(raw["width"])
                h = float(raw["height"])
                confidence = float(raw.get("confidence", 0.5))
            except (KeyError, TypeError, ValueError):
                continue
            if w <= 1 or h <= 1 or x < 0 or y < 0:
                continue
            if width and x + w > width + 1:
                continue
            if height and y + h > height + 1:
                continue
            label = raw.get("label")
            if not isinstance(label, str) or not label.strip():
                continue
            regions.append(
                VisualRegion(
                    id=str(raw.get("id") or f"v{index + 1}"),
                    label=label.strip(),
                    kind=str(raw.get("kind") or "control"),
                    bounds=Rect(x, y, w, h),
                    confidence=max(0.0, min(1.0, confidence)),
                    interactive=True,
                    source="openrouter-vision",
                )
            )
        return regions, summary
