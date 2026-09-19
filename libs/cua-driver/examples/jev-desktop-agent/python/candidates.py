from __future__ import annotations

import re

from contracts import ABSTAIN, DONE, REOBSERVE, Candidate, Element, Observation, VisualRegion
from policy import apply_risk, field_is_sensitive
from writer import PreparedText

CLICK_ROLES = {
    "button",
    "pushbutton",
    "togglebutton",
    "checkbox",
    "checkbutton",
    "link",
    "hyperlink",
    "listitem",
    "treeitem",
    "menuitem",
    "radiobutton",
    "tab",
    "pagetab",
    "tabitem",
    "combobox",
    "option",
}
TEXT_ROLES = {
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
PASSIVE_ROLES = {
    "label",
    "static",
    "statictext",
    "separator",
    "filler",
    "image",
    "icon",
    "paragraph",
    "heading",
}
ACTIVATION_WORDS = {
    "click",
    "press",
    "activate",
    "invoke",
    "open",
    "toggle",
    "select",
    "jump",
}


def _role_key(role: str) -> str:
    value = re.sub(r"[^a-z]", "", role.casefold())
    return value[2:] if value.startswith("ax") else value


def _words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.casefold())
        if len(word) > 1
    }


def _score(
    goal_words: set[str],
    label: str,
    role: str,
    index: int,
) -> tuple[int, int, int]:
    overlap = len(goal_words & _words(label))
    role_bonus = 1 if _role_key(role) in CLICK_ROLES | TEXT_ROLES else 0
    return overlap, role_bonus, -index


def _window_target(observation: Observation) -> dict[str, object]:
    return {
        "target": {
            "kind": "window",
            "pid": observation.pid,
            "window_id": observation.window_id,
        },
        "delivery_mode": "background",
    }


def _element_target(
    observation: Observation,
    element: Element,
) -> dict[str, object]:
    target = _window_target(observation)
    if element.token:
        target["element_token"] = element.token
    else:
        target["element_index"] = element.index
        target["snapshot_id"] = observation.snapshot_id
    return target


def _is_clickable(element: Element) -> bool:
    role = _role_key(element.role)
    if role in CLICK_ROLES:
        return True
    if role in PASSIVE_ROLES:
        return False
    actions = " ".join(element.actions).casefold()
    return any(word in actions for word in ACTIVATION_WORDS)


def _is_typeable(element: Element) -> bool:
    return _role_key(element.role) in TEXT_ROLES


def _hotkey_candidates(
    goal: str,
    observation: Observation,
) -> list[Candidate]:
    normalized = " ".join(re.findall(r"[a-z0-9]+", goal.casefold()))
    mapping: list[tuple[tuple[str, ...], str, list[str], str]] = [
        (("new tab",), "hotkey-new-tab", ["ctrl", "t"], "Open a new tab."),
        (
            ("address bar", "location bar", "url bar"),
            "hotkey-address",
            ["ctrl", "l"],
            "Focus the browser address bar.",
        ),
        (("close tab",), "hotkey-close-tab", ["ctrl", "w"], "Close the current tab."),
        (
            ("reopen tab",),
            "hotkey-reopen-tab",
            ["ctrl", "shift", "t"],
            "Reopen the last closed tab.",
        ),
        (("refresh", "reload"), "hotkey-reload", ["ctrl", "r"], "Reload the current view."),
        (("save",), "hotkey-save", ["ctrl", "s"], "Save the current document."),
        (
            ("select all",),
            "hotkey-select-all",
            ["ctrl", "a"],
            "Select all in the current context.",
        ),
        (("copy",), "hotkey-copy", ["ctrl", "c"], "Copy the current selection."),
        (("paste",), "hotkey-paste", ["ctrl", "v"], "Paste clipboard contents."),
        (("go back", "back"), "hotkey-back", ["alt", "left"], "Go back."),
    ]
    out: list[Candidate] = []
    for needles, cid, keys, description in mapping:
        if any(needle in normalized for needle in needles):
            out.append(
                apply_risk(
                    Candidate(
                        cid,
                        description,
                        "hotkey",
                        {**_window_target(observation), "keys": keys},
                        snapshot_id=observation.snapshot_id,
                        source="shortcut",
                    )
                )
            )
    if any(word in normalized for word in ("enter", "submit", "confirm", "open")):
        out.append(
            apply_risk(
                Candidate(
                    "press-enter",
                    "Press Enter in the target window.",
                    "press_key",
                    {**_window_target(observation), "key": "return"},
                    snapshot_id=observation.snapshot_id,
                    source="shortcut",
                )
            )
        )
    if any(
        word in normalized
        for word in ("escape", "cancel", "dismiss", "close dialog")
    ):
        out.append(
            Candidate(
                "press-escape",
                "Press Escape in the target window.",
                "press_key",
                {**_window_target(observation), "key": "escape"},
                snapshot_id=observation.snapshot_id,
                source="shortcut",
            )
        )
    return out


def _scroll_candidates(
    goal: str,
    observation: Observation,
) -> list[Candidate]:
    normalized = goal.casefold()
    out: list[Candidate] = []
    if "scroll" in normalized or "below" in normalized or "down" in normalized:
        out.append(
            Candidate(
                "scroll-down",
                "Scroll the target window down.",
                "scroll",
                {
                    **_window_target(observation),
                    "direction": "down",
                    "amount": 5,
                },
                snapshot_id=observation.snapshot_id,
                source="scroll",
            )
        )
    if "scroll" in normalized or "above" in normalized or "up" in normalized:
        out.append(
            Candidate(
                "scroll-up",
                "Scroll the target window up.",
                "scroll",
                {
                    **_window_target(observation),
                    "direction": "up",
                    "amount": 5,
                },
                snapshot_id=observation.snapshot_id,
                source="scroll",
            )
        )
    return out


def _visual_candidate(
    observation: Observation,
    region: VisualRegion,
) -> Candidate | None:
    if (
        not observation.capture_id
        or not region.interactive
        or region.confidence < 0.55
    ):
        return None
    x, y = region.bounds.center
    candidate = Candidate(
        id=f"visual-{region.id}",
        description=f'Activate visually grounded {region.kind} "{region.label}".',
        tool="click",
        arguments={
            "pid": observation.pid,
            "window_id": observation.window_id,
            "x": x,
            "y": y,
            "capture_id": observation.capture_id,
            "delivery_mode": "background",
        },
        snapshot_id=observation.snapshot_id,
        capture_id=observation.capture_id,
        source=region.source,
    )
    return apply_risk(candidate)


def build_candidates(
    goal: str,
    observation: Observation,
    *,
    prepared_texts: tuple[PreparedText, ...] = (),
    max_candidates: int = 32,
    allow_visual_clicks: bool = False,
) -> list[Candidate]:
    if max_candidates < 4:
        raise ValueError("max_candidates must leave room for terminal candidates")

    goal_words = _words(goal)
    elements = [
        element
        for element in observation.elements
        if element.enabled and element.label
    ]
    elements.sort(
        key=lambda element: _score(
            goal_words,
            element.label,
            element.role,
            element.index,
        ),
        reverse=True,
    )

    candidates: list[Candidate] = []
    action_limit = max_candidates - 3

    for element in elements:
        if len(candidates) >= action_limit:
            break
        if _is_clickable(element):
            candidates.append(
                apply_risk(
                    Candidate(
                        id=f"click-{element.index}",
                        description=f'Activate {element.role} "{element.label}".',
                        tool="click",
                        arguments=_element_target(observation, element),
                        snapshot_id=observation.snapshot_id,
                        source="semantic",
                    )
                )
            )
        if (
            _is_typeable(element)
            and prepared_texts
            and not field_is_sensitive(element.label)
        ):
            for slot in prepared_texts[:2]:
                if len(candidates) >= action_limit:
                    break
                candidates.append(
                    apply_risk(
                        Candidate(
                            id=f"type-{element.index}-{slot.id}",
                            description=(
                                f"Put prepared text {slot.id} into "
                                f'{element.role} "{element.label}".'
                            ),
                            tool="type_text",
                            arguments={
                                **_element_target(observation, element),
                                "text": slot.text,
                            },
                            snapshot_id=observation.snapshot_id,
                            source="semantic",
                        ),
                        field_label=element.label,
                    )
                )

    for extra in (
        _hotkey_candidates(goal, observation)
        + _scroll_candidates(goal, observation)
    ):
        if (
            len(candidates) < action_limit
            and extra.id not in {candidate.id for candidate in candidates}
        ):
            candidates.append(extra)

    if allow_visual_clicks:
        visual = sorted(
            observation.visual_regions,
            key=lambda region: (
                len(goal_words & _words(region.label)),
                region.confidence,
            ),
            reverse=True,
        )
        for region in visual:
            if len(candidates) >= action_limit:
                break
            candidate = _visual_candidate(observation, region)
            if candidate is not None:
                candidates.append(candidate)

    candidates.extend(
        [
            Candidate(
                DONE,
                "The current subgoal is already complete; stop acting on it.",
                None,
                {},
                source="terminal",
            ),
            Candidate(
                REOBSERVE,
                "Discard this decision set and obtain a fresh observation.",
                None,
                {},
                source="terminal",
            ),
            Candidate(
                ABSTAIN,
                "Stop without acting because none of the proposed actions is safe or useful.",
                None,
                {},
                source="terminal",
            ),
        ]
    )
    return candidates
