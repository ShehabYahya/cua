from __future__ import annotations

import re
import sys
from pathlib import Path

from contracts import (
    ABSTAIN,
    DONE,
    REOBSERVE,
    Candidate,
    Element,
    Observation,
    VisualRegion,
)
from policy import apply_risk, classify_risk, field_is_sensitive
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
    browser_search = bool(
        _words(observation.app) & {"firefox", "chrome", "chromium", "edge", "brave", "safari"}
        and _words(goal) & {"search", "navigate", "browse"}
    )
    browser_modifier = "cmd" if sys.platform == "darwin" else "ctrl"
    mapping: list[tuple[tuple[str, ...], str, list[str], str]] = [
        (("new tab",), "hotkey-new-tab", [browser_modifier, "t"], "Open a new tab."),
        (
            ("address bar", "location bar", "url bar"),
            "hotkey-address",
            [browser_modifier, "l"],
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
        (
            ("open file", "open document"),
            "hotkey-open-file",
            ["ctrl", "o"],
            "Open the application's file-open dialog.",
        ),
        (("next tab",), "hotkey-next-tab", ["ctrl", "tab"], "Switch to the next tab."),
        (
            ("previous tab", "prior tab"),
            "hotkey-previous-tab",
            ["ctrl", "shift", "tab"],
            "Switch to the previous tab.",
        ),
        (("go back", "back"), "hotkey-back", ["alt", "left"], "Go back."),
    ]
    out: list[Candidate] = []
    for needles, cid, keys, description in mapping:
        if any(needle in normalized for needle in needles) or (
            cid == "hotkey-address" and browser_search
        ):
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
    *,
    allow_visual_clicks: bool = False,
) -> Candidate | None:
    if not region.interactive or region.confidence < 0.55:
        return None

    # Canonical coordinate contract: when the caller opts in and a real
    # screenshot with pixel dimensions is current, the region center is already
    # in returned-PNG pixels (identity mapping; the Driver applies its own
    # scaling). No handcrafted OS scale conversion and no extra capture here.
    width = observation.screenshot_width or 0
    height = observation.screenshot_height or 0
    canonical = bool(
        allow_visual_clicks
        and width > 0
        and height > 0
        and observation.screenshot_frame_valid is not False
        and region.bounds.x >= 0
        and region.bounds.y >= 0
        and region.bounds.x + region.bounds.width <= width
        and region.bounds.y + region.bounds.height <= height
    )
    if not canonical and not observation.capture_id:
        return None

    x, y = region.bounds.center
    arguments: dict[str, object] = {
        **_window_target(observation),
        "x": x,
        "y": y,
    }
    if observation.capture_id:
        arguments["capture_id"] = observation.capture_id
    candidate = Candidate(
        id=f"visual-{region.id}",
        description=f'Activate visually grounded {region.kind} "{region.label}".',
        tool="click",
        arguments=arguments,
        snapshot_id=observation.snapshot_id,
        capture_id=observation.capture_id,
        source=region.source,
    )
    return apply_risk(candidate)


_BROWSER_APPS = {
    "firefox",
    "chrome",
    "chromium",
    "edge",
    "brave",
    "safari",
}


def _slot_note(slot: PreparedText) -> str:
    purpose = str(getattr(slot, "purpose", "") or "").strip()
    return f" ({purpose})" if purpose else ""


def _browser_binding(
    observation: Observation,
    element: Element,
) -> dict[str, object] | None:
    """Return the exact browser binding for a page control, or None.

    A browser control's ``element_index`` is a synthetic page address, so native
    ``type_text`` must never receive it. Such a control is usable only when the
    current tab advertises a complete binding.
    """
    if element.source != "browser" and not element.browser_ref:
        return None
    if not (
        element.browser_ref
        and observation.browser_target_id
        and observation.browser_tab_id
    ):
        return None
    return {
        "target_id": observation.browser_target_id,
        "tab_id": observation.browser_tab_id,
        "ref": element.browser_ref,
    }


def _bundle_field(
    goal_words: set[str],
    observation: Observation,
    *,
    enforce_policy: bool,
) -> Element | None:
    fields = [
        element
        for element in observation.elements
        if element.enabled
        and element.label
        and (_is_typeable(element) or "type" in element.actions)
        and (not enforce_policy or not field_is_sensitive(element.label))
        and (
            (element.source != "browser" and not element.browser_ref)
            or _browser_binding(observation, element) is not None
        )
    ]
    if not fields:
        return None
    fields.sort(
        key=lambda element: _score(
            goal_words,
            element.label,
            element.role,
            element.index,
            element.source,
        ),
        reverse=True,
    )
    return fields[0]


def _keyboard_step(
    cid: str,
    description: str,
    key: str,
    observation: Observation,
) -> Candidate:
    return apply_risk(
        Candidate(
            id=cid,
            description=description,
            tool="press_key",
            arguments={**_window_target(observation), "key": key},
            snapshot_id=observation.snapshot_id,
            source="keyboard",
        )
    )


def _hotkey_step(
    cid: str,
    description: str,
    keys: list[str],
    observation: Observation,
) -> Candidate:
    return apply_risk(
        Candidate(
            id=cid,
            description=description,
            tool="hotkey",
            arguments={**_window_target(observation), "keys": keys},
            snapshot_id=observation.snapshot_id,
            source="shortcut",
        )
    )


_RISK_SEVERITY = {"safe": 0, "confirm": 1, "deny": 2}


def _bundle_parent(
    *,
    cid: str,
    description: str,
    steps: tuple[Candidate, ...],
    observation: Observation,
) -> Candidate:
    """Build a sequence candidate whose risk reflects its child primitives."""
    risk = classify_risk(description, tool="sequence")
    for step in steps:
        if _RISK_SEVERITY.get(step.risk, 0) > _RISK_SEVERITY.get(risk, 0):
            risk = step.risk
    return Candidate(
        id=cid,
        description=description,
        tool="sequence",
        arguments={},
        snapshot_id=observation.snapshot_id,
        source="sequence",
        risk=risk,
        steps=steps,
    )


def _operation_bundles(
    goal: str,
    observation: Observation,
    prepared_texts: tuple[PreparedText, ...],
    *,
    enforce_policy: bool = False,
) -> list[Candidate]:
    """Locally built operation bundles that Jev may choose; never auto-run."""
    if not prepared_texts:
        return []

    normalized = goal.casefold()
    goal_words = _words(goal)
    field = _bundle_field(
        goal_words,
        observation,
        enforce_policy=enforce_policy,
    )
    window = _window_target(observation)
    browser_app = bool(_words(observation.app) & _BROWSER_APPS)
    modifier = "cmd" if sys.platform == "darwin" else "ctrl"
    out: list[Candidate] = []

    # Every supplied slot gets its own bundles; the provider-facing shortlist is
    # what stays bounded, not the local action pool.
    for slot in prepared_texts:
        note = _slot_note(slot)

        if field is not None:
            binding = _browser_binding(observation, field)
            if binding is not None:
                # `browser_type` calls DOM.focus on the exact ref and refuses
                # unless the node is the focused editable element
                # (cua-driver-core/src/browser/tools.rs:1632-1694), so the
                # focused page field receives the following Enter.
                type_step = apply_risk(
                    Candidate(
                        id=f"bundle-step-type-{field.index}-{slot.id}",
                        description=(
                            f"Put prepared text {slot.id}{note} into "
                            f'{field.role} "{field.label}".'
                        ),
                        tool="browser_type",
                        arguments={
                            **binding,
                            "text": slot.text,
                            "replace": True,
                        },
                        snapshot_id=observation.snapshot_id,
                        source="browser",
                    ),
                    field_label=field.label,
                )
                submit_step = _keyboard_step(
                    f"bundle-step-submit-{field.index}-{slot.id}",
                    "Press Enter in the target window to submit.",
                    "return",
                    observation,
                )
                out.append(
                    _bundle_parent(
                        cid=f"bundle-fill-submit-{field.index}-{slot.id}",
                        description=(
                            f'Fill {field.role} "{field.label}" with prepared text '
                            f"{slot.id}{note} and submit it with Enter."
                        ),
                        steps=(type_step, submit_step),
                        observation=observation,
                    )
                )
            # No native fill+submit bundle: the element-target native type_text
            # path does not establish real focus ("No focus steal", XSendEvent on
            # Linux, platform-linux/src/tools/impl_.rs:4135), so a following
            # window-level Enter is not proven to submit that field. The
            # standalone native type and Enter choices remain in build_candidates.

        if browser_app:
            address_step = _hotkey_step(
                f"bundle-step-address-{slot.id}",
                "Focus the browser address bar.",
                [modifier, "l"],
                observation,
            )
            search_type_step = apply_risk(
                Candidate(
                    id=f"bundle-step-search-type-{slot.id}",
                    description=(
                        f"Type prepared text {slot.id}{note} into the browser "
                        "address bar."
                    ),
                    tool="type_text",
                    arguments={**window, "text": slot.text},
                    snapshot_id=observation.snapshot_id,
                    source="keyboard",
                )
            )
            search_submit = _keyboard_step(
                f"bundle-step-search-submit-{slot.id}",
                "Press Enter to run the search.",
                "return",
                observation,
            )
            out.append(
                _bundle_parent(
                    cid=f"bundle-browser-search-{slot.id}",
                    description=(
                        "Focus the browser address bar, type prepared text "
                        f"{slot.id}{note}, and press Enter to search."
                    ),
                    steps=(address_step, search_type_step, search_submit),
                    observation=observation,
                )
            )

            if "new tab" in normalized:
                new_tab_step = _hotkey_step(
                    f"bundle-step-new-tab-{slot.id}",
                    "Open a new browser tab.",
                    [modifier, "t"],
                    observation,
                )
                new_tab_address = _hotkey_step(
                    f"bundle-step-new-tab-address-{slot.id}",
                    "Focus the address bar in the new tab.",
                    [modifier, "l"],
                    observation,
                )
                new_tab_type = apply_risk(
                    Candidate(
                        id=f"bundle-step-new-tab-type-{slot.id}",
                        description=(
                            f"Type prepared text {slot.id}{note} into the new "
                            "tab's address bar."
                        ),
                        tool="type_text",
                        arguments={**window, "text": slot.text},
                        snapshot_id=observation.snapshot_id,
                        source="keyboard",
                    )
                )
                new_tab_submit = _keyboard_step(
                    f"bundle-step-new-tab-submit-{slot.id}",
                    "Press Enter to run the search in the new tab.",
                    "return",
                    observation,
                )
                out.append(
                    _bundle_parent(
                        cid=f"bundle-browser-new-tab-search-{slot.id}",
                        description=(
                            "Open a new browser tab, focus the address bar, type "
                            f"prepared text {slot.id}{note}, and press Enter to "
                            "search."
                        ),
                        steps=(
                            new_tab_step,
                            new_tab_address,
                            new_tab_type,
                            new_tab_submit,
                        ),
                        observation=observation,
                    )
                )
    return out


def build_candidates(
    goal: str,
    observation: Observation,
    *,
    prepared_texts: tuple[PreparedText, ...] = (),
    max_candidates: int = 32,
    allow_visual_clicks: bool = False,
    download_root: str | None = None,
    recent_files: tuple[str, ...] = (),
    enforce_policy: bool = False,
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
    # Large trees often start with hundreds of menu items. Reserve the best
    # editable controls before filling the action budget with click routes.
    if prepared_texts:
        text_fields = [
            element
            for element in elements
            if (_is_typeable(element) or "type" in element.actions)
            and (
                not enforce_policy
                or not field_is_sensitive(element.label)
            )
        ][:4]
        text_indices = {element.index for element in text_fields}
        elements = text_fields + [e for e in elements if e.index not in text_indices]

    candidates: list[Candidate] = []
    action_limit = max_candidates - 3
    normalized_goal = goal.casefold()

    # Keep intent-specific keyboard/scroll routes from being crowded out by
    # large accessibility trees. These are often the most reliable recovery
    # paths on browsers and native Wayland.
    priority_extras = _hotkey_candidates(goal, observation) + _scroll_candidates(goal, observation)
    for extra in priority_extras[:8]:
        if len(candidates) >= action_limit:
            break
        if extra.id not in {candidate.id for candidate in candidates}:
            candidates.append(extra)

    # Concrete multi-primitive operations are reserved before the bulk menu so
    # Jev can still see them on a large tree. They are offers, never auto-run.
    for bundle in _operation_bundles(
        goal,
        observation,
        prepared_texts,
        enforce_policy=enforce_policy,
    ):
        if len(candidates) >= action_limit:
            break
        candidates.append(bundle)

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
        and (not enforce_policy or not field_is_sensitive(goal))
    ):
        for slot in prepared_texts:
            if len(candidates) >= action_limit:
                break
            candidates.append(
                Candidate(
                    id=f"type-focused-{slot.id}",
                    description=(
                        f"Type prepared text {slot.id}{_slot_note(slot)} into the "
                        "currently focused editable control in the target window."
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
                and (
                    not enforce_policy
                    or not field_is_sensitive(element.label)
                )
            ):
                for slot in prepared_texts:
                    if len(candidates) >= action_limit:
                        break
                    candidates.append(
                        apply_risk(
                            Candidate(
                                id=(
                                    f"browser-type-{element.index}-{slot.id}"
                                ),
                                description=(
                                    f"Put prepared text {slot.id}"
                                    f"{_slot_note(slot)} into page "
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
            and (
                not enforce_policy
                or not field_is_sensitive(element.label)
            )
        ):
            for slot in prepared_texts:
                if len(candidates) >= action_limit:
                    break
                candidates.append(
                    apply_risk(
                        Candidate(
                            id=f"type-{element.index}-{slot.id}",
                            description=(
                                f"Put prepared text {slot.id}"
                                f"{_slot_note(slot)} into "
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
        and (not enforce_policy or not field_is_sensitive(goal))
    ):
        for slot in prepared_texts:
            if len(candidates) >= action_limit:
                break
            candidates.append(
                Candidate(
                    id=f"type-focused-{slot.id}",
                    description=(
                        f"Type prepared text {slot.id}{_slot_note(slot)} into the "
                        "current editable/focused control in the target window."
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
            candidate = _visual_candidate(
                observation,
                region,
                allow_visual_clicks=allow_visual_clicks,
            )
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


_TERMINAL_IDS = {DONE, REOBSERVE, ABSTAIN}
_SHORTLIST_CAPS = {
    "session": 6,
    "bundle": 6,
    "keyboard": 6,
    "typing": 6,
}


def shortlist_candidates(
    goal: str,
    candidates: list[Candidate],
    *,
    limit: int = 32,
    page: int = 0,
) -> list[Candidate]:
    """Pick one bounded, deterministic, paged candidate set for a Jev round.

    This is a purely local final selection: no Jev/group calls. Terminals and a
    bounded reserve of session operations, operation bundles, keyboard recovery,
    and typing are always kept. `done` and `abstain` are unconditional, and at
    least one real action is reserved whenever one exists, so a tiny limit can
    never return terminals alone. With two or more real slots the first stays
    reserved for session operations/bundles/keyboard/typing and the rest page
    over the remaining builder pool; with exactly one real slot that slot pages
    across the whole selectable pool, so repeated `page` values still expose
    different real actions and large trees never lose the tail permanently.
    """
    if limit < 4:
        raise ValueError("shortlist limit must be at least 4")
    page = max(0, int(page))
    goal_words = _words(goal)

    unique: list[Candidate] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        if candidate.id in seen_ids:
            continue
        seen_ids.add(candidate.id)
        unique.append(candidate)

    terminals = [
        candidate
        for candidate in unique
        if candidate.id in _TERMINAL_IDS or candidate.source == "terminal"
    ]
    terminal_ids = {candidate.id for candidate in terminals}
    actions = [
        candidate for candidate in unique if candidate.id not in terminal_ids
    ]
    # The paging escape hatch is not an executable action; hold it back so it
    # can never consume the slot reserved for a real action below.
    selectable = [
        candidate for candidate in actions if candidate.id != "more-actions"
    ]
    more_actions = [
        candidate for candidate in actions if candidate.id == "more-actions"
    ]

    def is_bundle(candidate: Candidate) -> bool:
        return bool(candidate.steps)

    def is_typing(candidate: Candidate) -> bool:
        return (
            candidate.tool in {"type_text", "browser_type"}
            or candidate.id.startswith("type-")
            or candidate.id.startswith("type-focused-")
            or candidate.id.startswith("browser-type-")
        )

    def is_keyboard(candidate: Candidate) -> bool:
        if is_bundle(candidate) or is_typing(candidate):
            return False
        return (
            candidate.source in {"shortcut", "keyboard"}
            or candidate.tool in {"press_key", "hotkey"}
        )

    # Keep `done` and `abstain` unconditionally; reserve room for a real action
    # and for `more-actions` before optional terminals such as `reobserve`.
    required_terminals = [
        candidate for candidate in terminals if candidate.id in {DONE, ABSTAIN}
    ]
    optional_terminals = [
        candidate for candidate in terminals if candidate.id not in {DONE, ABSTAIN}
    ]
    budget = max(0, limit - len(required_terminals))
    reserved_non_terminal = (1 if selectable else 0) + (1 if more_actions else 0)
    optional_room = max(0, budget - reserved_non_terminal)
    terminals = required_terminals + optional_terminals[:optional_room]
    remaining = max(0, limit - len(terminals))

    session_pool = [
        candidate for candidate in selectable if candidate.source == "session"
    ]
    pools = (
        (session_pool, _SHORTLIST_CAPS["session"]),
        ([c for c in selectable if is_bundle(c)], _SHORTLIST_CAPS["bundle"]),
        ([c for c in selectable if is_keyboard(c)], _SHORTLIST_CAPS["keyboard"]),
        ([c for c in selectable if is_typing(c)], _SHORTLIST_CAPS["typing"]),
    )

    more_actions_slot = (
        1
        if more_actions and remaining >= (2 if selectable else 1)
        else 0
    )
    real_room = remaining - more_actions_slot
    # Reserve a fixed slot only when at least two real slots exist, leaving the
    # remainder for the paged tail. With a single real slot there is nothing to
    # split: page that slot across the whole selectable pool so repeated `page`
    # values expose different real actions instead of the same reserved one.
    reserved_room = max(0, real_room - 1)

    chosen: list[Candidate] = []
    chosen_ids: set[str] = set()
    for pool, cap in pools:
        taken = 0
        for candidate in pool:
            if taken >= cap or len(chosen) >= reserved_room:
                break
            if candidate.id in chosen_ids:
                continue
            chosen.append(candidate)
            chosen_ids.add(candidate.id)
            taken += 1

    tail = [
        candidate for candidate in selectable if candidate.id not in chosen_ids
    ]
    ordered_tail = [
        candidate
        for _, candidate in sorted(
            enumerate(tail),
            key=lambda item: (
                len(goal_words & _words(item[1].description)),
                2 if item[1].source == "browser" else 0,
                -item[0],
            ),
            reverse=True,
        )
    ]

    tail_room = max(0, real_room - len(chosen))
    window: list[Candidate] = []
    if ordered_tail and tail_room > 0:
        stride = min(tail_room, len(ordered_tail))
        start = (page * stride) % len(ordered_tail)
        window = [
            ordered_tail[(start + offset) % len(ordered_tail)]
            for offset in range(stride)
        ]

    selected = chosen + window
    if more_actions_slot:
        selected = more_actions[:1] + selected
    return selected + terminals
