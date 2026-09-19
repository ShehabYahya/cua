from __future__ import annotations

import json
import re
from dataclasses import dataclass

from contracts import Observation, PlanStep
from openrouter_client import DEFAULT_REASONING_MODEL, OpenRouterClient
from policy import field_is_sensitive


@dataclass(frozen=True)
class PreparedText:
    id: str
    text: str
    source: str = "user"


def quoted_text_slots(goal: str, *, limit: int = 4) -> tuple[PreparedText, ...]:
    slots: list[PreparedText] = []
    i = 0
    while i < len(goal) and len(slots) < limit:
        quote = goal[i]
        if quote not in {'"', "'"}:
            i += 1
            continue
        end = goal.find(quote, i + 1)
        if end < 0:
            break
        value = goal[i + 1 : end].strip()
        if value:
            slots.append(PreparedText(f"text-{len(slots) + 1}", value, "user"))
        i = end + 1
    return tuple(slots)


def inferred_text_slots(
    goal: str,
    *,
    limit: int = 2,
) -> tuple[PreparedText, ...]:
    quoted = quoted_text_slots(goal, limit=limit)
    if quoted:
        return quoted

    patterns = (
        (
            "search",
            r"\bsearch(?:\s+the\s+(?:web|internet))?\s+for\s+(.+?)"
            r"(?=,\s*(?:and|then)\b|\s+(?:and|then)\s+stop\b|$)",
        ),
        (
            "rename",
            r"\brename\b.+?\bto\s+([^,]+?)"
            r"(?=,\s*(?:and|then)\b|$)",
        ),
    )
    for source, pattern in patterns:
        match = re.search(pattern, goal, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip(" \t\r\n.?!")
        if value:
            return (PreparedText("text-1", value, f"user-{source}"),)
    return ()


def redact_prepared_text(goal: str, slots: tuple[PreparedText, ...]) -> str:
    redacted = goal
    for slot in slots:
        if slot.text:
            redacted = redacted.replace(slot.text, f"<{slot.id}>")
    return redacted


def _needs_text(goal: str) -> bool:
    words = set(re.findall(r"[a-z]+", goal.casefold()))
    return bool(
        words
        & {
            "type",
            "write",
            "enter",
            "search",
            "reply",
            "message",
            "name",
            "rename",
            "compose",
            "fill",
        }
    )


class OpenRouterWriter:
    def __init__(
        self,
        client: OpenRouterClient,
        *,
        model: str = DEFAULT_REASONING_MODEL,
    ) -> None:
        self.client = client
        self.model = model

    async def prepare(
        self,
        *,
        original_goal: str,
        step: PlanStep,
        observation: Observation,
    ) -> tuple[PreparedText, ...]:
        import asyncio

        user_slots = inferred_text_slots(original_goal)
        slots: list[PreparedText] = list(user_slots)
        if step.text and all(item.text != step.text for item in slots):
            slots.append(PreparedText(f"text-{len(slots) + 1}", step.text, "planner"))
        if slots or not _needs_text(step.goal):
            return tuple(slots)
        editable = [
            {"role": e.role, "label": e.label}
            for e in observation.elements
            if e.enabled and e.label and not field_is_sensitive(e.label)
        ][:20]
        if not editable:
            return tuple(slots)
        try:
            generated = await asyncio.to_thread(
                self._compose_sync,
                original_goal,
                step,
                editable,
            )
        except Exception:
            generated = None
        if generated:
            slots.append(PreparedText("text-1", generated, "writer"))
        return tuple(slots)

    def _compose_sync(
        self,
        original_goal: str,
        step: PlanStep,
        editable: list[dict[str, str]],
    ) -> str | None:
        prompt = f"""Original user goal: {original_goal}
Current subgoal: {step.goal}
Editable fields: {json.dumps(editable, ensure_ascii=False)}

If the user intends ordinary text to be typed for this subgoal, return JSON {{"text":"exact text"}}. If no text should be generated, return {{"text":null}}. Never generate passwords, passcodes, OTPs, payment-card data, private keys, seed phrases, or recovery codes. Preserve quoted user text exactly rather than paraphrasing it.
"""
        body = self.client.chat_json(
            system="Generate only the ordinary text a user requested to place in a UI field. Return JSON only.",
            prompt=prompt,
            model=self.model,
            max_tokens=500,
        )
        if not isinstance(body, dict):
            return None
        value = body.get("text")
        return value if isinstance(value, str) and value.strip() else None
