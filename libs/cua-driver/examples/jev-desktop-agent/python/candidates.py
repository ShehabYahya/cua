from __future__ import annotations

import re

from contracts import ABSTAIN, REOBSERVE, Candidate, Element, Observation
from writer import PreparedText

CLICK_ROLES = {
    "button",
    "checkbox",
    "link",
    "listitem",
    "menuitem",
    "radiobutton",
    "tab",
    "tabitem",
}
TEXT_ROLES = {"combobox", "edit", "searchfield", "textarea", "textbox", "textfield"}


def _role_key(role: str) -> str:
    value = re.sub(r"[^a-z]", "", role.lower())
    return value[2:] if value.startswith("ax") else value


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 1}


def _score(goal_words: set[str], element: Element) -> tuple[int, int]:
    label_words = _words(element.label)
    overlap = len(goal_words & label_words)
    return overlap, -element.index


def _target(observation: Observation, element: Element) -> dict[str, object]:
    target: dict[str, object] = {
        "target": {
            "kind": "window",
            "pid": observation.pid,
            "window_id": observation.window_id,
        },
        "delivery_mode": "background",
    }
    if element.token:
        target["element_token"] = element.token
    else:
        target["element_index"] = element.index
        target["snapshot_id"] = observation.snapshot_id
    return target


def build_candidates(
    goal: str,
    observation: Observation,
    *,
    prepared_texts: tuple[PreparedText, ...] = (),
    max_candidates: int = 32,
) -> list[Candidate]:
    if max_candidates < 2:
        raise ValueError("max_candidates must leave room for reobserve and abstain")

    goal_words = _words(goal)
    usable = [
        element for element in observation.elements if element.enabled and element.label
    ]
    usable.sort(key=lambda element: _score(goal_words, element), reverse=True)

    candidates: list[Candidate] = []
    action_limit = max_candidates - 2
    for element in usable:
        if len(candidates) >= action_limit:
            break
        role = _role_key(element.role)
        target = _target(observation, element)

        if role in CLICK_ROLES:
            candidates.append(
                Candidate(
                    id=f"click-{element.index}",
                    description=f'Activate {element.role} "{element.label}".',
                    tool="click",
                    arguments=target,
                    snapshot_id=observation.snapshot_id,
                )
            )
            continue

        if role in TEXT_ROLES and prepared_texts:
            slot = prepared_texts[0]
            candidates.append(
                Candidate(
                    id=f"type-{element.index}-{slot.id}",
                    description=(
                        f'Put prepared text {slot.id} into {element.role} "{element.label}".'
                    ),
                    tool="type_text",
                    arguments={**target, "text": slot.text},
                    snapshot_id=observation.snapshot_id,
                )
            )

    candidates.extend(
        [
            Candidate(
                REOBSERVE,
                "Discard this action table and obtain a fresh desktop observation.",
                None,
                {},
            ),
            Candidate(
                ABSTAIN,
                "Stop without acting because none of the proposed actions is safe or useful.",
                None,
                {},
            ),
        ]
    )
    return candidates
