from __future__ import annotations

import re
from pathlib import Path

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
    source: str,
) -> tuple[int, int, int, int]:
    overlap = len(goal_words & _words(label))
    source_bonus = 2 if source == "browser" else 0
    role_bonus = 1 if _role_key(role) in CLICK_ROLES | TEXT_ROLES else 0
    return overlap, source_bonus, role_bonus, -index


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
    # Carry both modern and legacy snapshot addresses. The Driver adapter
    # filters to the exact schema advertised by the installed version.
    if element.token:
        target["element_token"] = element.token
    target["element_index"] = element.index
    target["snapshot_id"] = observation.snapshot_id
    return target


def _is_clickable(element: Element) -> bool:
    role = _role_key(element.role)
    if role in CLICK_ROLES:
        return True
    actions = " ".join(element.actions).casefold()
    if any(word in actions for word in ACTIVATION_WORDS):
        return True
    if role in PASSIVE_ROLES:
        return False
    return False


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
        (("undo",), "hotkey-undo", ["ctrl", "z"], "Undo the last action."),
        (("redo",), "hotkey-redo", ["ctrl", "shift", "z"], "Redo the last undone action."),
        (("new window",), "hotkey-new-window", ["ctrl", "n"], "Open a new window."),
        (("open file", "open document"), "hotkey-open-file", ["ctrl", "o"], "Open the application's file-open dialog."),
        (("next tab",), "hotkey-next-tab", ["ctrl", "tab"], "Switch to the next tab."),
        (("previous tab", "prior tab"), "hotkey-previous-tab", ["ctrl", "shift", "tab"], "Switch to the previous tab."),
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
    if any(
        word in normalized
        for word in (
            "enter",
            "submit",
            "confirm",
            "open",
            "search",
            "navigate",
            "rename",
        )
    ):
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
    if "rename" in normalized:
        out.append(
            Candidate(
                "press-rename",
                "Press F2 to rename the current selection.",
                "press_key",
                {**_window_target(observation), "key": "f2"},
                snapshot_id=observation.snapshot_id,
                source="shortcut",
            )
        )
    if "delete" in normalized or "remove" in normalized:
        out.append(
            apply_risk(
                Candidate(
                    "press-delete",
                    "Delete the current selection.",
                    "press_key",
                    {**_window_target(observation), "key": "delete"},
                    snapshot_id=observation.snapshot_id,
                    source="shortcut",
                )
            )
        )
    if "next field" in normalized or "tab to" in normalized:
        out.append(
            Candidate(
                "press-tab",
                "Move keyboard focus to the next field.",
                "press_key",
                {**_window_target(observation), "key": "tab"},
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
    download_root: str | None = None,
    recent_files: tuple[str, ...] = (),
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
            element.source,
        ),
        reverse=True,
    )

    candidates: list[Candidate] = []
    action_limit = max_candidates - 3
    normalized_goal = goal.casefold()

    # Keep intent-specific keyboard/scroll routes from being crowded out by
    # large accessibility trees. These are often the most reliable recovery
    # paths on browsers and native Wayland.
    priority_extras = (
        _hotkey_candidates(goal, observation)
        + _scroll_candidates(goal, observation)
    )
    for extra in priority_extras[:8]:
        if len(candidates) >= action_limit:
            break
        if extra.id not in {candidate.id for candidate in candidates}:
            candidates.append(extra)

    if (
        prepared_texts
        and any(
            word in normalized_goal
            for word in (
                "type",
                "write",
                "enter",
                "search",
                "query",
                "command",
                "paste",
            )
        )
        and not field_is_sensitive(goal)
        and len(candidates) < action_limit
    ):
        slot = prepared_texts[0]
        candidates.append(
            Candidate(
                id=f"type-focused-{slot.id}",
                description=(
                    f"Type prepared text {slot.id} into the currently "
                    "focused editable control in the target window."
                ),
                tool="type_text",
                arguments={
                    **_window_target(observation),
                    "text": slot.text,
                },
                snapshot_id=observation.snapshot_id,
                source="keyboard",
            )
        )

    for element in elements:
        if len(candidates) >= action_limit:
            break

        if (
            element.source == "browser"
            and element.browser_ref
            and observation.browser_target_id
            and observation.browser_tab_id
        ):
            browser_common = {
                "target_id": observation.browser_target_id,
                "tab_id": observation.browser_tab_id,
                "ref": element.browser_ref,
            }
            pointer_requested = {
                "right_click": (
                    "right click" in normalized_goal
                    or "context menu" in normalized_goal
                ),
                "double_click": "double click" in normalized_goal,
                "hover": (
                    "hover" in normalized_goal
                    or "mouse over" in normalized_goal
                ),
            }
            if "pointer" in element.actions:
                for pointer_action, wanted in pointer_requested.items():
                    if not wanted or len(candidates) >= action_limit:
                        continue
                    candidates.append(
                        apply_risk(
                            Candidate(
                                id=(
                                    f"browser-{pointer_action}-"
                                    f"{element.index}"
                                ),
                                description=(
                                    f"{pointer_action.replace('_', ' ').title()} "
                                    f'page {element.role} "{element.label}".'
                                ),
                                tool="browser_pointer",
                                arguments={
                                    **browser_common,
                                    "action": pointer_action,
                                    "input_route": "dom_event",
                                },
                                snapshot_id=observation.snapshot_id,
                                source="browser",
                            )
                        )
                    )
            if (
                ("scroll" in element.actions or "pointer" in element.actions)
                and "scroll" in normalized_goal
                and len(candidates) < action_limit
            ):
                direction = -650 if "up" in normalized_goal else 650
                candidates.append(
                    Candidate(
                        id=f"browser-scroll-{element.index}",
                        description=(
                            f'Scroll page region "{element.label}" '
                            f'{"up" if direction < 0 else "down"}.'
                        ),
                        tool="browser_pointer",
                        arguments={
                            **browser_common,
                            "action": "scroll",
                            "delta_y": direction,
                            "input_route": "dom_event",
                        },
                        snapshot_id=observation.snapshot_id,
                        source="browser",
                    )
                )

            if (
                "click" in element.actions
                and download_root
                and "download" in normalized_goal
                and len(candidates) < action_limit
            ):
                candidates.append(
                    apply_risk(
                        Candidate(
                            id=f"browser-download-{element.index}",
                            description=(
                                f'Download from page {element.role} '
                                f'"{element.label}" into the approved '
                                "download directory."
                            ),
                            tool="browser_download",
                            arguments={
                                **browser_common,
                                "destination_root": download_root,
                            },
                            snapshot_id=observation.snapshot_id,
                            source="browser",
                        )
                    )
                )

            if "click" in element.actions:
                candidates.append(
                    apply_risk(
                        Candidate(
                            id=f"browser-click-{element.index}",
                            description=(
                                f'Activate page {element.role} "{element.label}".'
                            ),
                            tool="browser_click",
                            arguments={
                                **browser_common,
                                "input_route": "dom_event",
                            },
                            snapshot_id=observation.snapshot_id,
                            source="browser",
                        )
                    )
                )
            if (
                "upload" in element.actions
                and recent_files
                and any(
                    word in normalized_goal
                    for word in ("attach", "upload", "file", "document", "pdf")
                )
            ):
                for file_index, file_path in enumerate(recent_files[:4], start=1):
                    if len(candidates) >= action_limit:
                        break
                    name = Path(file_path).name
                    candidates.append(
                        apply_risk(
                            Candidate(
                                id=(
                                    f"browser-upload-{element.index}-"
                                    f"{file_index}"
                                ),
                                description=(
                                    f'Attach recent local file "{name}" '
                                    f'using page {element.role} '
                                    f'"{element.label}".'
                                ),
                                tool="browser_set_input_files",
                                arguments={
                                    **browser_common,
                                    "files": [file_path],
                                },
                                snapshot_id=observation.snapshot_id,
                                source="browser",
                            )
                        )
                    )

            if (
                "type" in element.actions
                and prepared_texts
                and not field_is_sensitive(element.label)
            ):
                for slot in prepared_texts[:2]:
                    if len(candidates) >= action_limit:
                        break
                    candidates.append(
                        apply_risk(
                            Candidate(
                                id=(
                                    f"browser-type-{element.index}-{slot.id}"
                                ),
                                description=(
                                    f"Put prepared text {slot.id} into page "
                                    f'{element.role} "{element.label}".'
                                ),
                                tool="browser_type",
                                arguments={
                                    **browser_common,
                                    "text": slot.text,
                                    "replace": True,
                                },
                                snapshot_id=observation.snapshot_id,
                                source="browser",
                            ),
                            field_label=element.label,
                        )
                    )
            continue

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
            if "double click" in normalized_goal or (
                "open" in normalized_goal
                and _role_key(element.role) in {
                    "listitem",
                    "treeitem",
                    "tablecell",
                    "row",
                }
            ):
                candidates.append(
                    apply_risk(
                        Candidate(
                            id=f"double-click-{element.index}",
                            description=f'Double-click {element.role} "{element.label}".',
                            tool="double_click",
                            arguments=_element_target(observation, element),
                            snapshot_id=observation.snapshot_id,
                            source="semantic",
                        )
                    )
                )
            if "right click" in normalized_goal or "context menu" in normalized_goal:
                candidates.append(
                    Candidate(
                        id=f"right-click-{element.index}",
                        description=f'Open the context menu for {element.role} "{element.label}".',
                        tool="right_click",
                        arguments=_element_target(observation, element),
                        snapshot_id=observation.snapshot_id,
                        source="semantic",
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

    has_type_candidate = any(
        candidate.id.startswith("type-")
        or candidate.id.startswith("browser-type-")
        for candidate in candidates
    )
    normalized_goal = goal.casefold()
    if (
        prepared_texts
        and not has_type_candidate
        and any(
            word in normalized_goal
            for word in ("type", "write", "enter", "command", "paste")
        )
        and not field_is_sensitive(goal)
        and len(candidates) < action_limit
    ):
        slot = prepared_texts[0]
        candidates.append(
            Candidate(
                id=f"type-focused-{slot.id}",
                description=(
                    f"Type prepared text {slot.id} into the current "
                    "editable/focused control in the target window."
                ),
                tool="type_text",
                arguments={
                    **_window_target(observation),
                    "text": slot.text,
                },
                snapshot_id=observation.snapshot_id,
                source="keyboard",
            )
        )

    if "drag" in normalized_goal and len(candidates) < action_limit:
        pointer_elements = [
            element
            for element in elements
            if (
                element.source == "browser"
                and element.browser_ref
                and "pointer" in element.actions
            )
        ][:8]
        for source in pointer_elements:
            for destination in pointer_elements:
                if source.index == destination.index:
                    continue
                if len(candidates) >= action_limit:
                    break
                candidates.append(
                    apply_risk(
                        Candidate(
                            id=(
                                f"browser-drag-{source.index}-"
                                f"{destination.index}"
                            ),
                            description=(
                                f'Drag page "{source.label}" to '
                                f'"{destination.label}".'
                            ),
                            tool="browser_pointer",
                            arguments={
                                "target_id": observation.browser_target_id,
                                "tab_id": observation.browser_tab_id,
                                "ref": source.browser_ref,
                                "destination_ref": destination.browser_ref,
                                "action": "drag",
                                "input_route": "dom_event",
                            },
                            snapshot_id=observation.snapshot_id,
                            source="browser",
                        )
                    )
                )
            if len(candidates) >= action_limit:
                break

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

    urls = re.findall(r"https?://[^\s'\"<>]+", goal)
    if (
        urls
        and observation.browser_target_id
        and observation.browser_tab_id
        and len(candidates) < action_limit
    ):
        candidates.append(
            Candidate(
                "browser-navigate",
                f"Navigate the current browser tab to {urls[0]}.",
                "browser_navigate",
                {
                    "target_id": observation.browser_target_id,
                    "tab_id": observation.browser_tab_id,
                    "url": urls[0],
                },
                snapshot_id=observation.snapshot_id,
                source="browser",
            )
        )

    candidates = candidates[:action_limit]

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
