from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Activity:
    category: str   # one of CATEGORIES
    label: str      # short verb, from LABELS
    text: str       # ONE human sentence. Never contains ids/indices/confidence.


CATEGORIES = ("look", "click", "type", "keys", "scroll", "switch",
              "launch", "refresh", "options", "compose", "finish", "stop", "other")

LABELS = {"look": "Look", "click": "Click", "type": "Type", "keys": "Keys",
          "scroll": "Scroll", "switch": "Switch", "launch": "Launch",
          "refresh": "Refresh", "options": "More options", "compose": "Compose", "finish": "Finish",
          "stop": "Stop", "other": "Action"}

# Candidate ids such as click-3, visual-v2, type-2-slot-1.
_ID_TOKEN = re.compile(r"\b(?:[a-z]+-)+\d+(?:-[a-z0-9]+)*\b")
_QUOTED = re.compile(r'"([^"]*)"')
_SINGLE_QUOTED = re.compile(r"'([^']*)'")
_COMBO = re.compile(r"\b[A-Za-z]+(?:\+[A-Za-z0-9]+)+\b")
_KINDS = {"button", "link", "tab", "menu", "icon", "checkbox"}
_CLICK_TOOLS = {"click", "double_click", "right_click", "browser_click"}
_CLICK_IDS = ("click-", "browser-click-", "browser-pointer-", "browser-drag-")
_TYPE_TOOLS = {"type_text", "browser_type"}
_TYPE_IDS = ("type-", "browser-type-", "bundle-browser-search-",
             "bundle-browser-new-tab-search-", "bundle-fill-submit-")
_CANNED = {
    "finish": "Reported the task complete.",
    "stop": "Stopped: nothing available was safe or useful.",
    "refresh": "Took a fresh look at the window.",
    "options": "Showed more available actions.",
    "compose": "Composed the text this step needed.",
    "type": "Typed the prepared text into the focused field.",
    "scroll": "Scrolled the view.",
}


def _strip_ids(text: str) -> str:
    return " ".join(_ID_TOKEN.sub(" ", text).split())


def _label_and_kind(description: str, *, allow_single: bool = False) -> tuple[str, str]:
    """First quoted label plus its optional preceding kind word.

    ``allow_single`` additionally accepts single-quoted spans: the loop formats
    switch/launch descriptions with ``!r``, so real window and application names
    arrive single-quoted. Click labels always come from candidates.py, which
    double-quotes them, so that path stays double-quote only and cannot be
    tripped by an apostrophe in a label.
    """
    match = _QUOTED.search(description)
    if match is None and allow_single:
        match = _SINGLE_QUOTED.search(description)
    if match is None:
        return "", ""
    label = _strip_ids(match.group(1).strip())
    before = description[: match.start()].rstrip().split()
    kind = before[-1].strip(",.;:").casefold() if before else ""
    return label, (kind if kind in _KINDS else "")


def _category(selected_id: str, tool: str | None) -> str:
    if selected_id == "done":
        return "finish"
    if selected_id == "abstain":
        return "stop"
    if selected_id == "reobserve":
        return "refresh"
    if selected_id == "more-actions":
        return "options"
    if selected_id == "inspect-screen":
        return "look"
    if selected_id == "discover-apps":
        return "look"
    if selected_id == "prepare-text":
        return "compose"
    if selected_id.startswith("switch-window-"):
        return "switch"
    if selected_id.startswith("launch-app-"):
        return "launch"
    if selected_id.startswith("visual-"):
        return "click"
    if tool in _CLICK_TOOLS or selected_id.startswith(_CLICK_IDS):
        return "click"
    if tool in _TYPE_TOOLS or selected_id.startswith(_TYPE_IDS):
        return "type"
    if tool in {"press_key", "hotkey"} or selected_id.startswith(("hotkey-", "press-")):
        return "keys"
    if tool == "scroll" or selected_id.startswith("scroll-"):
        return "scroll"
    return "other"


def describe(*, selected_id: str, tool: str | None, description: str) -> Activity:
    """Map one loop candidate to a chat-ready activity row."""
    selected_id = selected_id or ""
    description = description or ""
    category = _category(selected_id, tool)
    if category in _CANNED:
        text = _CANNED[category]
    elif category == "look":
        text = ("Checked which applications are installed."
                if selected_id == "discover-apps"
                else "Captured the window to find the controls.")
    elif category == "switch":
        title = _strip_ids(_label_and_kind(description, allow_single=True)[0])
        text = f'Switched to "{title}".' if title else "Switched to another window."
    elif category == "launch":
        name = _strip_ids(_label_and_kind(description, allow_single=True)[0])
        text = f'Opened "{name}".' if name else "Opened an application."
    elif category == "click":
        label, kind = _label_and_kind(description)
        if not label:
            text = "Clicked a control on the screen."
        else:
            text = f"Clicked the {label} {kind}." if kind else f"Clicked the {label}."
    elif category == "keys":
        combo = _COMBO.search(description)
        text = f"Pressed {combo.group(0)}." if combo else "Pressed a keyboard shortcut."
    else:
        text = _strip_ids(description) or "Took an action."
    return Activity(category, LABELS[category], text)


def closing_line(status: str, message: str) -> str:
    """Human sentence that closes a turn, from a RunResult status."""
    status = str(status or "")
    message = message if isinstance(message, str) else ""
    if status == "completed":
        return "Done."
    if status == "abstained":
        return "I stopped without acting: nothing I could see was safe or useful."
    if status == "budget_exhausted":
        return "I ran out of steps before finishing."
    if status == "cancelled":
        return "Cancelled."
    if status == "dry_run":
        return "Dry run: I stopped before acting."
    if status in {"refused", "blocked"}:
        return message or "I stopped."
    return message or "Something went wrong."
