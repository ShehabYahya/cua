from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedText:
    id: str
    text: str


def quoted_text_slots(goal: str, *, limit: int = 4) -> tuple[PreparedText, ...]:
    """Extract caller-supplied quoted text without asking Jev to generate text."""
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
            slots.append(PreparedText(f"text-{len(slots) + 1}", value))
        i = end + 1
    return tuple(slots)


def redact_prepared_text(goal: str, slots: tuple[PreparedText, ...]) -> str:
    """Replace prepared values with stable slot IDs before a decision request."""
    redacted = goal
    for slot in slots:
        for quote in ('"', "'"):
            redacted = redacted.replace(f"{quote}{slot.text}{quote}", f"<{slot.id}>")
    return redacted
