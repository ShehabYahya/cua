from __future__ import annotations

import re

from contracts import Candidate

DENY_FIELD_WORDS = {
    "password",
    "passcode",
    "pin",
    "cvv",
    "cvc",
    "security code",
    "one time password",
    "otp",
    "recovery code",
    "private key",
    "seed phrase",
}

CONFIRM_WORDS = {
    "send",
    "download",
    "submit",
    "publish",
    "post",
    "upload",
    "attach",
    "share",
    "authorize",
    "approve",
    "follow",
    "like",
    "subscribe",
    "delete",
    "remove",
    "trash",
    "erase",
    "pay",
    "purchase",
    "buy",
    "order",
    "checkout",
    "transfer",
    "wire",
    "install",
    "uninstall",
    "allow",
    "grant",
    "permission",
    "sign in",
    "signin",
    "log in",
    "login",
    "logout",
    "log out",
    "reboot",
    "restart computer",
    "shut down",
    "shutdown",
    "factory reset",
}


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _has_phrase(normalized: str, phrase: str) -> bool:
    return f" {phrase} " in f" {normalized} "


def field_is_sensitive(label: str) -> bool:
    normalized = _normalized(label)
    return any(_has_phrase(normalized, word) for word in DENY_FIELD_WORDS)


def sensitive_text_intent(text: str) -> bool:
    normalized = _normalized(text)
    if not any(_has_phrase(normalized, word) for word in DENY_FIELD_WORDS):
        return False
    return any(
        _has_phrase(normalized, verb)
        for verb in (
            "enter",
            "type",
            "fill",
            "write",
            "paste",
            "input",
            "use",
            "set",
            "submit",
        )
    )


def classify_risk(description: str, *, tool: str | None, field_label: str = "") -> str:
    if tool == "type_text" and field_is_sensitive(field_label):
        return "deny"
    normalized = _normalized(description)
    if any(_has_phrase(normalized, word) for word in CONFIRM_WORDS):
        return "confirm"
    return "safe"


def apply_risk(candidate: Candidate, *, field_label: str = "") -> Candidate:
    risk = classify_risk(candidate.description, tool=candidate.tool, field_label=field_label)
    if risk == candidate.risk:
        return candidate
    return Candidate(
        candidate.id,
        candidate.description,
        candidate.tool,
        candidate.arguments,
        snapshot_id=candidate.snapshot_id,
        capture_id=candidate.capture_id,
        source=candidate.source,
        risk=risk,
    )
