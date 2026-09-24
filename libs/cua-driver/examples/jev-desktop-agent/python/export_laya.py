"""Strict, offline Laya export and local telemetry maintenance.

No provider calls, automatic labels, network uploads, retraining or deployment.
Export requires a closed, loss-free session and reviewed, independently labelled
exact inputs. Jev-derived data is unconditionally excluded by this implementation.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import re
import sqlite3
import time
from collections import Counter
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterator

from telemetry import CHOICE_INSTRUCTIONS, FORMATTER, SQL, TOOLS, TelemetryConfig, delete_command, identifier, private_directory, private_file, store_bytes
from telemetry_labels import ChoiceLabel, append_label


class Ineligible(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise Ineligible(reason)


def training_row(request: dict, label: dict, *, complete: bool, session_sealed: bool) -> dict:
    """Validate one pre-decision record. Predictions/effects are never model input."""
    require(complete and session_sealed, "incomplete_trace")
    provenance = request.get("provenance", {})
    require(provenance.get("collection_mode") == "training", "capture_not_opted_in")
    require(provenance.get("jev_derived") is False and provenance.get("teacher_involved") is False, "disallowed_or_unknown_provenance")
    require(provenance.get("provider") in {"laya", "user_demonstration", "controlled_fixture"}, "disallowed_provider")
    require(request.get("input_representation") == "exact", "not_exact_model_input")
    require(request.get("formatter_version") == FORMATTER, "incompatible_formatter")
    require(request.get("redaction_status") == "allowlisted", "redaction_failure")
    require(request.get("capture_truncated") is False, "truncated_capture")
    allowed_label_keys = {f.name for f in fields(ChoiceLabel)}
    require(set(label) <= allowed_label_keys, "unknown_label_fields")
    try:
        reviewed = ChoiceLabel(**label)
        reviewed.validate()
    except (TypeError, ValueError):
        raise Ineligible("invalid_label") from None
    require(reviewed.status == "verified" and reviewed.correction_scope == "original_decision", "no_original_independent_label")
    require(reviewed.input_reviewed and reviewed.training_rights_reviewed, "missing_input_or_rights_review")
    audit = request.get("token_audit", {})
    require(audit.get("status") == "measured" and audit.get("option_markers_complete") is True, "missing_token_audit")
    require(audit.get("builder_version") == FORMATTER, "incompatible_token_builder")
    context = request.get("model_context", {})
    for key in ("checkpoint_revision", "tokenizer_revision", "laya_code_revision"):
        require(isinstance(context.get(key), str) and bool(re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", context[key])), "unpinned_model_context")
    model_input = request.get("model_input", {})
    require(set(model_input) == {"state", "questions"}, "unexpected_model_input")
    state = model_input["state"]
    require(isinstance(state, dict) and set(state) == {"goal_alias", "constraints", "observation", "history"}, "input_schema_or_future_state_leakage")
    # Do not recursively accept arbitrary keys claiming to be harmless metadata.
    require(isinstance(state["history"], list) and not state["history"], "history_provenance_not_reviewed")
    constraints = state["constraints"]
    require(isinstance(constraints, dict) and set(constraints) <= {"submit_requested", "use_existing_tab", "append_requested", "replace_requested", "typing_requested", "interpretation"}, "invalid_constraints")
    for key, value in constraints.items():
        require(value is None or isinstance(value, bool) or (key == "interpretation" and value == "heuristic_not_ground_truth"), "unsafe_constraint")
    require(isinstance(state["goal_alias"], str) and bool(re.fullmatch(r"t[0-9]+", state["goal_alias"])), "unsafe_goal")
    _validate_observation(state["observation"])
    questions = model_input["questions"]
    require(isinstance(questions, dict) and set(questions) == {"driver_action"}, "unexpected_questions")
    question = questions["driver_action"]
    require(set(question) == {"type", "instructions", "criteria"} and question["type"] == "choice", "invalid_choice_question")
    require(question["instructions"] == CHOICE_INSTRUCTIONS, "instruction_mismatch")
    criteria = question["criteria"]
    require(isinstance(criteria, dict) and 2 <= len(criteria) <= 32, "invalid_choice_count")
    candidate_context = request.get("candidate_context", {})
    order = candidate_context.get("shown_order")
    mapping = candidate_context.get("alias_to_local_candidate", {})
    require(list(criteria) == order == list(mapping), "candidate_order_or_mapping_mismatch")
    require(order == [f"a{i}" for i in range(len(order))], "invalid_request_aliases")
    require(len(set(mapping.values())) == len(mapping), "duplicate_candidate_binding")
    require(all(isinstance(value, str) and re.fullmatch(r"c[0-9]{1,6}", value) for value in mapping.values()), "unsafe_candidate_binding")
    require(all(isinstance(value, str) and re.fullmatch(r"[a-z_]+ t[0-9]+", value) for value in criteria.values()), "unsafe_candidate_description")
    require(all(value.split()[0] in TOOLS | {"session_or_unknown"} for value in criteria.values()), "unsafe_action_family")
    require(audit.get("marker_count") == len(order) and audit.get("option_order") == order, "option_marker_mismatch")
    retained = audit.get("option_tokens_retained")
    require(isinstance(retained, dict) and list(retained) == order and all(type(n) is int and n > 0 for n in retained.values()), "missing_retained_options")
    for key in ("state_tokens_before", "state_tokens_retained"):
        require(type(audit.get(key)) is int and audit[key] >= 0, "invalid_state_token_audit")
    require(audit["state_tokens_retained"] <= audit["state_tokens_before"], "invalid_state_token_audit")
    local_ids = set(mapping.values())
    acceptable = set(reviewed.acceptable_ids)
    require(bool(acceptable) and acceptable <= local_ids, "gold_not_in_shown_set")
    if reviewed.target_distribution is None:
        require(len(acceptable) == 1, "ambiguous_single_label")
        weights = {key: float(key in acceptable) for key in local_ids}
    else:
        require(reviewed.preference_reviewed, "unreviewed_soft_target")
        weights = dict(reviewed.target_distribution)
        require(set(weights) == local_ids and {key for key, value in weights.items() if value > 0} == acceptable, "target_key_or_support_mismatch")
    require(all(math.isfinite(v) and v >= 0 for v in weights.values()) and math.isclose(sum(weights.values()), 1, abs_tol=1e-6), "invalid_target_mass")
    return {"state": state, "questions": questions,
            "gold": {"driver_action": {"probabilities": {alias: weights[mapping[alias]] for alias in order}}}}


def _validate_observation(obs: object) -> None:
    from telemetry import ROLES, SOURCES
    require(isinstance(obs, dict) and set(obs) == {"snapshot_id", "window", "app", "tab", "elements", "degraded", "truncated", "omitted_elements", "visual_regions_present", "read_error_present"}, "unsafe_observation_schema")
    for key, prefix in (("snapshot_id", "s"), ("window", "w"), ("app", "t"), ("tab", "b")):
        value = obs[key]
        require(value is None or (isinstance(value, str) and bool(re.fullmatch(prefix + r"[0-9]+", value))), "unsafe_observation_identity")
    require(obs["omitted_elements"] == 0 and obs["truncated"] is not True, "truncated_observation")
    for key in ("degraded", "truncated", "visual_regions_present", "read_error_present"):
        require(obs[key] is None or isinstance(obs[key], bool), "unsafe_observation_flag")
    require(isinstance(obs["elements"], list) and len(obs["elements"]) <= 192, "invalid_elements")
    for element in obs["elements"]:
        require(isinstance(element, dict) and set(element) == {"id", "name", "role", "source", "focused", "visible", "enabled", "selected", "checked", "expanded"}, "unsafe_element_schema")
        for key, prefix in (("id", "e"), ("name", "t")):
            value = element[key]
            require(value is None or (isinstance(value, str) and bool(re.fullmatch(prefix + r"[0-9]+", value))), "unsafe_element_identity")
        require(element["role"] in ROLES and element["source"] in SOURCES, "unsafe_element_metadata")
        require(all(element[key] is None or isinstance(element[key], bool) for key in ("focused", "visible", "enabled", "selected", "checked", "expanded")), "unsafe_element_flags")


@contextlib.contextmanager
def open_store(root: Path) -> Iterator[sqlite3.Connection]:
    import fcntl
    root = private_directory(root)
    require((root / "traces.sqlite3").is_file(), "telemetry_store_missing")
    private_file(root / "writer.lock")
    private_file(root / "traces.sqlite3")
    with (root / "writer.lock").open("r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Ineligible("stop_porter_before_maintenance") from None
        db = sqlite3.connect(root / "traces.sqlite3", timeout=0.1)
        try:
            db.execute("PRAGMA secure_delete=ON")
            db.executescript(SQL)
            # Register exports before writing their bytes. Interrupted exports stay
            # associated with the source episodes and remain deletable.
            yield db
            db.commit()
        finally:
            db.close()


def export_store(root: Path, *, json_strings: bool = False) -> dict:
    root = root.expanduser().absolute()
    with open_store(root) as db:
        rows, manifest_rows, rejected = [], [], Counter()
        requests = db.execute("SELECT decision_id, command_id, session_id, created, body FROM events WHERE kind='decision_requested' ORDER BY created, sequence").fetchall()
        for did, cid, sid, created, body in requests:
            session = db.execute("SELECT sealed,drops FROM sessions WHERE id=?", (sid,)).fetchone()
            ended = db.execute("SELECT COUNT(*) FROM events WHERE command_id=? AND kind='command_ended'", (cid,)).fetchone()[0]
            started = db.execute("SELECT COUNT(*) FROM events WHERE command_id=? AND kind='command_started'", (cid,)).fetchone()[0]
            decisions = db.execute("SELECT COUNT(*) FROM events WHERE decision_id=? AND kind='decision_requested'", (did,)).fetchone()[0]
            outcome = db.execute("SELECT COUNT(*) FROM events WHERE decision_id=? AND kind='decision_outcome'", (did,)).fetchone()[0]
            label = db.execute("SELECT body FROM labels WHERE decision_id=? ORDER BY rowid DESC LIMIT 1", (did,)).fetchone()
            try:
                require(label is not None, "missing_independent_label")
                row = training_row(json.loads(body)["data"], json.loads(label[0]), complete=started == 1 and ended == 1 and decisions == 1 and outcome == 1, session_sealed=bool(session and session[0] and not session[1]))
            except (Ineligible, KeyError, TypeError, ValueError) as error:
                reason = str(error) if isinstance(error, Ineligible) else "invalid_trace_schema"
                rejected[reason] += 1
                continue
            digest = hashlib.sha256(json.dumps(row["state"], sort_keys=True).encode()).hexdigest()
            rows.append({key: json.dumps(value, separators=(",", ":")) for key, value in row.items()} if json_strings else row)
            manifest_rows.append({"decision_id": did, "command_id": cid, "session_id": sid,
                                  "captured_at": created, "duplicate_group": digest})
        # Do not create misleading empty training files.
        if not rows:
            return {"exported": 0, "rejected": dict(rejected), "files": []}
        # Keep the capture chronological and unsplit. The manifest binds all rows
        # to whole sessions/commands/duplicate groups for downstream group splitting.
        output_text = "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows)
        manifest_text = json.dumps({"formatter_version": FORMATTER, "created_at": time.time(),
                                    "split_policy": "unsplit_group_by_session_command_and_duplicate_then_chronological_holdout",
                                    "rows": manifest_rows, "rejected": dict(rejected)}, indent=2)
        configured = db.execute("SELECT value FROM settings WHERE key='max_store_bytes'").fetchone()
        budget = int(configured[0]) if configured else TelemetryConfig().max_store_bytes
        require(store_bytes(db, root) + len(output_text.encode()) + len(manifest_text.encode()) + 16384 <= budget, "export_storage_budget_exceeded")
        eid = identifier()
        destination = private_directory(root / "exports")
        commands = sorted({row["command_id"] for row in manifest_rows})
        output = destination / f"{eid}.jsonl"
        manifest = destination / f"{eid}.json"
        for path in (output, manifest):
            db.execute("INSERT INTO exports VALUES(?,?,?)", (identifier(), path.name, json.dumps(commands)))
        db.commit()
        for path in (output, manifest):
            private_file(path)
        output.write_text(output_text)
        manifest.write_text(manifest_text)
        return {"exported": len(rows), "rejected": dict(rejected), "files": [str(output), str(manifest)]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=TelemetryConfig().path())
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export")
    export.add_argument("--json-strings", action="store_true")
    delete = sub.add_parser("delete-command")
    delete.add_argument("command_id")
    label = sub.add_parser("label")
    label.add_argument("decision_id")
    label.add_argument("label_file", type=Path)
    args = parser.parse_args(argv)
    args.directory = args.directory.expanduser().absolute()
    try:
        if args.action == "export":
            result = export_store(args.directory, json_strings=args.json_strings)
        else:
            with open_store(args.directory) as db:
                if args.action == "delete-command":
                    require(bool(re.fullmatch(r"[a-f0-9]{32}", args.command_id)), "invalid_command_id")
                    delete_command(db, args.directory, args.command_id)
                    db.commit()
                    db.execute("VACUUM")
                    result = {"deleted_command": args.command_id}
                else:
                    payload = json.loads(args.label_file.read_text())
                    result = {"label_revision": append_label(db, args.decision_id, ChoiceLabel(**payload))}
        print(json.dumps(result))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        # No paths, label contents, SQL details or exception payloads in diagnostics.
        print(json.dumps({"error": "telemetry_operation_rejected"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
