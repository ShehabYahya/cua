"""Independent, content-free evidence and append-only human label revisions.

A successful effect is NOT a gold action. Choice labels require a separate
review of the original offered candidates, not the model's prediction.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Mapping

from telemetry import identifier

FAILURES = frozenset("transcription payload_preparation perception candidate_generation shortlisting choice execution delayed_effect verification policy_cancellation unknown".split())
LABEL_SOURCES = frozenset({"human_review", "user_demonstration", "independent_checker", "controlled_fixture"})


@dataclass(frozen=True)
class Evidence:
    status: str
    checker_version: str
    predicates: Mapping[str, bool | None]


def _outcome(predicates: Mapping[str, bool | None], settled: bool) -> Evidence:
    if not settled:
        status = "pending"
    elif any(value is False for value in predicates.values()):
        status = "verified_failure"
    elif predicates and all(value is True for value in predicates.values()):
        status = "verified_success"
    else:
        status = "unknown"
    return Evidence(status, "firefox-evidence-v1", dict(predicates))


def verify_typing(*, expected: str | None, actual: str | None,
                  correct_target: bool | None, submit_requested: bool | None,
                  submissions_delta: int | None, settled: bool = False) -> Evidence:
    """Compare payload only in memory; caller must establish the checked interval."""
    predicates = {"correct_target": correct_target,
                  "payload_matches": actual == expected if actual is not None and expected is not None else None}
    if submit_requested is False:
        predicates["no_submission_in_interval"] = submissions_delta == 0 if submissions_delta is not None else None
    return _outcome(predicates, settled)


def verify_submission(*, requested: bool | None, correct_conversation: bool | None,
                      matching_new_messages: int | None, total_new_user_messages: int | None,
                      settled: bool = False) -> Evidence:
    # A cleared composer, successful Enter call, or visual change is insufficient.
    return _outcome({"submission_requested": requested,
                     "correct_conversation": correct_conversation,
                     "one_matching_new_message": matching_new_messages == 1 if matching_new_messages is not None else None,
                     "no_duplicate_submission": total_new_user_messages == 1 if total_new_user_messages is not None else None}, settled)


def verify_focus(*, intended_control_focused: bool | None, settled: bool = False) -> Evidence:
    return _outcome({"intended_control_focused": intended_control_focused}, settled)


def verify_existing_tab(*, intended_tab_active: bool | None, tab_count_delta: int | None,
                        settled: bool = False) -> Evidence:
    return _outcome({"intended_tab_active": intended_tab_active,
                     "no_duplicate_tab": tab_count_delta == 0 if tab_count_delta is not None else None}, settled)


def verify_task(requirements: Mapping[str, Evidence]) -> str:
    if not requirements:
        return "unknown"
    statuses = {e.status for e in requirements.values()}
    if "verified_failure" in statuses:
        return "verified_failure"
    if "pending" in statuses:
        return "pending"
    return "verified_success" if statuses == {"verified_success"} else "unknown"


def classify_candidate_failure(correct_ids: set[str], pool_ids: set[str], shown_ids: set[str]) -> str:
    if not correct_ids:
        return "unknown"
    if not correct_ids & pool_ids:
        return "candidate_generation"
    if not correct_ids & shown_ids:
        return "shortlisting"
    return "choice"


@dataclass(frozen=True)
class ChoiceLabel:
    source: str
    acceptable_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    status: str = "verified"
    failure_category: str = "unknown"
    target_distribution: Mapping[str, float] | None = None
    preference_reviewed: bool = False
    input_reviewed: bool = False
    training_rights_reviewed: bool = False
    correction_scope: str = "original_decision"
    version: str = "choice-review-v1"

    def validate(self) -> None:
        if self.source not in LABEL_SOURCES or self.status not in {"verified", "pending", "retracted"}:
            raise ValueError("invalid independent label source or status")
        if self.failure_category not in FAILURES or self.version != "choice-review-v1":
            raise ValueError("invalid label taxonomy/version")
        if self.correction_scope not in {"original_decision", "changed_intention", "transcription"}:
            raise ValueError("invalid correction scope")
        if len(set(self.acceptable_ids)) != len(self.acceptable_ids) or len(self.acceptable_ids) > 32:
            raise ValueError("invalid acceptable candidate set")
        if any(not re.fullmatch(r"c[0-9]{1,6}", key) for key in self.acceptable_ids):
            raise ValueError("labels must reference captured local candidate aliases")
        if not self.evidence_refs or len(self.evidence_refs) > 32 or any(not re.fullmatch(r"[a-f0-9]{32}", ref) for ref in self.evidence_refs):
            raise ValueError("independent evidence references are required")
        for value in (self.preference_reviewed, self.input_reviewed, self.training_rights_reviewed):
            if not isinstance(value, bool):
                raise ValueError("review flags must be boolean")
        if self.target_distribution is not None:
            if len(self.target_distribution) > 32:
                raise ValueError("target distribution exceeds option budget")
            for key, value in self.target_distribution.items():
                if not re.fullmatch(r"c[0-9]{1,6}", key) or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError("invalid independent target distribution")
            if not math.isclose(sum(self.target_distribution.values()), 1.0, abs_tol=1e-6):
                raise ValueError("target mass must equal one")


def append_label(db: sqlite3.Connection, decision_id: str, label: ChoiceLabel) -> str:
    label.validate()
    if not re.fullmatch(r"[a-f0-9]{32}", decision_id):
        raise ValueError("invalid decision reference")
    exists = db.execute("SELECT 1 FROM events WHERE decision_id=? AND kind='decision_requested'", (decision_id,)).fetchone()
    if not exists:
        raise ValueError("unknown decision; do not label a later recovery as an earlier state")
    # Evidence IDs may refer to external reviewed evidence, not just runtime events.
    # Source and reviewer attestations remain explicit; they are never inferred.
    lid = identifier()
    db.execute("INSERT INTO labels VALUES(?,?,?,?)", (lid, decision_id, time.time(), json.dumps(asdict(label), allow_nan=False)))
    return lid
