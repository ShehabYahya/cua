from __future__ import annotations

import json
import re
from dataclasses import dataclass

from contracts import Element, Observation, PlanStep, StepRecord
from openrouter_client import DEFAULT_REASONING_MODEL, OpenRouterClient
from policy import field_is_sensitive


@dataclass(frozen=True)
class PreparedText:
    id: str
    text: str
    source: str = "user"
    purpose: str = ""


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
            slots.append(
                PreparedText(
                    f"text-{len(slots) + 1}",
                    value,
                    "user",
                    "quoted user text",
                )
            )
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
            "search query",
        ),
        (
            "rename",
            r"\brename\b.+?\bto\s+([^,]+?)"
            r"(?=,\s*(?:and|then)\b|$)",
            "new name",
        ),
    )
    for source, pattern, purpose in patterns:
        match = re.search(pattern, goal, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip(" \t\r\n.?!")
        if value:
            return (
                PreparedText("text-1", value, f"user-{source}", purpose),
            )
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


_EDITABLE_ROLES = {
    "combobox",
    "edit",
    "entry",
    "searchfield",
    "textarea",
    "textbox",
    "textfield",
    "textentry",
    "passwordtext",
}


def _role_key(role: str) -> str:
    value = re.sub(r"[^a-z]", "", role.casefold())
    return value[2:] if value.startswith("ax") else value


def _is_editable(element: Element) -> bool:
    return _role_key(element.role) in _EDITABLE_ROLES or "type" in element.actions


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
            slots.append(
                PreparedText(
                    f"text-{len(slots) + 1}",
                    step.text,
                    "planner",
                    "planned field text",
                )
            )
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
            slots.append(
                PreparedText(
                    "text-1",
                    generated,
                    "writer",
                    "generated field text",
                )
            )
        return tuple(slots)

    async def compose(
        self,
        *,
        original_goal: str,
        observation: Observation,
        history: list[StepRecord],
        existing: tuple[PreparedText, ...],
    ) -> tuple[PreparedText, ...]:
        """Ask the companion chat client for additional ordinary text slots.

        Invoked only when Jev selects prepare-text, never before every action.
        Returns new, distinct, nonempty slots with stable increasing text-N IDs
        after ``existing``. All typed content stays local to these slots. On any
        failure it returns () rather than reporting fabricated success.
        """
        import asyncio

        # Password-role controls are never offered to the writer, even when the
        # label itself does not match the sensitive-word list.
        password_roles = {
            "passwordtext",
            "password",
            "passwd",
            "passfield",
            "securetextfield",
        }
        editable = [
            element
            for element in observation.elements
            if element.enabled
            and element.label
            and _is_editable(element)
            and _role_key(element.role) not in password_roles
            and not field_is_sensitive(element.label)
        ]
        # Focused controls first (focus is distinct from selection), then the
        # most goal-relevant labels; the order stays stable for ties.
        goal_words = set(re.findall(r"[a-z0-9]+", original_goal.casefold()))
        editable.sort(
            key=lambda element: (
                0 if element.focused is True else 1,
                -len(
                    goal_words
                    & set(re.findall(r"[a-z0-9]+", element.label.casefold()))
                ),
            )
        )
        editable_fields: list[dict[str, object]] = []
        for element in editable[:8]:
            entry: dict[str, object] = {
                "role": element.role,
                "label": element.label,
            }
            if isinstance(element.value, str) and element.value.strip():
                entry["value"] = element.value[:4000]
            editable_fields.append(entry)
        window_context: dict[str, object] = {
            "app": observation.app,
            "window": observation.window_title,
            "recent_context": list(observation.recent_context[-8:]),
        }
        action_history = [
            {
                "selected_id": record.selected_id,
                "description": record.description,
                "outcome": record.outcome,
            }
            for record in history[-8:]
        ]
        existing_purposes = [
            {"id": slot.id, "purpose": slot.purpose or slot.source}
            for slot in existing
        ]
        try:
            generated = await asyncio.to_thread(
                self._compose_additional_sync,
                original_goal,
                window_context,
                editable_fields,
                action_history,
                existing_purposes,
            )
        except Exception:
            return ()
        if not generated:
            return ()

        start = 0
        for slot in existing:
            match = re.fullmatch(r"text-(\d+)", slot.id)
            if match:
                start = max(start, int(match.group(1)))
        seen_texts = {slot.text for slot in existing if slot.text}
        new_slots: list[PreparedText] = []
        for text, purpose in generated:
            if text in seen_texts:
                continue
            if len(new_slots) >= 4:
                break
            start += 1
            new_slots.append(
                PreparedText(f"text-{start}", text, "writer", purpose)
            )
            seen_texts.add(text)
        return tuple(new_slots)

    def _compose_additional_sync(
        self,
        original_goal: str,
        window_context: dict[str, object],
        editable_fields: list[dict[str, object]],
        action_history: list[dict[str, str]],
        existing_purposes: list[dict[str, str]],
    ) -> list[tuple[str, str]] | None:
        prompt = f"""Original user goal: {original_goal}
Current app/window and recent context: {json.dumps(window_context, ensure_ascii=False)}
Target editable fields with existing ordinary text (sensitive fields excluded): {json.dumps(editable_fields, ensure_ascii=False)}
Recent action history: {json.dumps(action_history, ensure_ascii=False)}
Existing prepared text slots: {json.dumps(existing_purposes, ensure_ascii=False)}

Request additional ordinary text the user still needs for the current command. Never generate passwords, passcodes, OTPs, payment-card data, private keys, seed phrases, or recovery codes. Return JSON only: {{"texts":[{{"text":"exact text","purpose":"short label"}}]}} with at most 4 entries. Return {{"texts":[]}} if no additional ordinary text is needed.
"""
        body = self.client.chat_json(
            system="Propose only ordinary text a user requested to place in UI fields. Return JSON only.",
            prompt=prompt,
            model=self.model,
            max_tokens=800,
        )
        if not isinstance(body, dict):
            return None
        raw = body.get("texts")
        if not isinstance(raw, list):
            return None
        out: list[tuple[str, str]] = []
        for item in raw[:4]:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            purpose = item.get("purpose")
            if not isinstance(text, str) or not text.strip():
                continue
            label = purpose.strip() if isinstance(purpose, str) else ""
            out.append((text.strip(), label))
        return out

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
