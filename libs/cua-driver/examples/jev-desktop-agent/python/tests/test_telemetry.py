from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telemetry import (CHOICE_INSTRUCTIONS, FORMATTER, Projection, SQL, Telemetry,
                       TelemetryConfig, delete_command, identifier, intent)
from telemetry_labels import (ChoiceLabel, append_label, classify_candidate_failure,
                              verify_existing_tab, verify_focus, verify_submission,
                              verify_task, verify_typing)
from export_laya import Ineligible, export_store, open_store, training_row


def fixture():
    """Independently authored structural fixture, NOT an observed Laya example."""
    observation = Projection().observation(SimpleNamespace(elements=(), snapshot_id="fixture", pid=1, window_id=2))
    observation["truncated"] = False
    request = {
        "provenance": {"collection_mode": "training", "provider": "controlled_fixture",
                       "jev_derived": False, "teacher_involved": False},
        "input_representation": "exact", "formatter_version": FORMATTER,
        "redaction_status": "allowlisted", "capture_truncated": False,
        "model_input": {
            "state": {"goal_alias": "t0", "constraints": {"submit_requested": False},
                      "observation": observation, "history": []},
            "questions": {"driver_action": {"type": "choice", "instructions": CHOICE_INSTRUCTIONS,
                                             "criteria": {"a0": "click t1", "a1": "type_text t2"}}}},
        "candidate_context": {"shown_order": ["a0", "a1"], "alias_to_local_candidate": {"a0": "c7", "a1": "c3"}},
        "token_audit": {"status": "measured", "builder_version": FORMATTER,
                        "option_markers_complete": True, "marker_count": 2,
                        "option_order": ["a0", "a1"], "option_tokens_retained": {"a0": 3, "a1": 4},
                        "state_tokens_before": 12, "state_tokens_retained": 12},
        "model_context": {key: "a" * 40 for key in ("checkpoint_revision", "tokenizer_revision", "laya_code_revision")}}
    label = ChoiceLabel("controlled_fixture", ("c3",), ("b" * 32,), input_reviewed=True, training_rights_reviewed=True)
    return request, label


def drain(telemetry):
    result = []
    while not telemetry._queue.empty():
        result.append(json.loads(telemetry._queue.get_nowait()[-1]))
    return result


class CaptureTests(unittest.TestCase):
    def test_off_creates_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "absent"
            t = Telemetry(TelemetryConfig(directory=root))
            t.start()
            t.begin_command(identifier(), "private secret")
            t.end_command("completed")
            t.close()
            self.assertFalse(root.exists())
            self.assertEqual(drain(t), [])

    def test_training_requires_explicit_consent(self):
        with self.assertRaises(ValueError):
            TelemetryConfig(mode="training")
        with patch.dict(os.environ, {"PORTER_TELEMETRY_MODE": "off"}, clear=True):
            self.assertEqual(TelemetryConfig.from_env().mode, "off")

    def test_queue_is_bounded_and_drops_are_visible(self):
        t = Telemetry(TelemetryConfig(mode="operational", queue_size=1))
        t.begin_command(identifier(), "secret")
        before = time.monotonic()
        for _ in range(1000):
            t.cancel_requested()
        self.assertLess(time.monotonic() - before, 1)
        self.assertEqual(t._queue.qsize(), 1)
        self.assertEqual(t.status()["dropped_events"], 1000)
        t.close()

    def test_oversized_event_dropped_not_truncated_silently(self):
        t = Telemetry(TelemetryConfig(mode="operational", max_event_bytes=1024))
        t._record("test", {"constant": "x" * 2000})
        self.assertEqual(t.status()["dropped_events"], 1)
        self.assertEqual(drain(t), [])

    def test_operational_mode_contains_no_command_or_voice_model(self):
        t = Telemetry(TelemetryConfig(mode="operational"))
        uid = identifier()
        t.begin_command(identifier(), "SECRET_PATIENT_123", source="voice", utterance_id=uid)
        t.voice_timing(uid, 15, status="transcribed", model="SECRET_MODEL", language="SECRET_LANGUAGE")
        t.end_command("completed")
        rows = drain(t)
        self.assertNotIn("SECRET", json.dumps(rows))
        self.assertEqual(rows[-1]["data"]["task_verified"], "unknown")
        self.assertEqual(rows[1]["data"]["stt_quality"], None)
        self.assertEqual(rows[1]["data"]["utterance_id"], uid)

    def test_redaction_consistent_and_aliases_reset_per_command(self):
        t = Telemetry(TelemetryConfig(mode="training", training_consent=True))
        t.begin_command(identifier(), "SECRET_COMMAND")
        projection = t.projection
        candidate = SimpleNamespace(id="SECRET_ID", description="SECRET_TITLE", tool="type_text",
                                    arguments={"text": "SECRET_PAYLOAD", "ref": "SECRET_REF", "element_index": 4}, steps=())
        obs = SimpleNamespace(elements=(SimpleNamespace(index=4, label="SECRET_TITLE", role="textbox", value="SECRET_PASSWORD"),),
                              snapshot_id="SECRET_SNAPSHOT", pid=123, window_id=456, app="SECRET_APP",
                              browser_url="https://private.invalid/?SECRET_QUERY", screenshot_path="SECRET_IMAGE")
        safe = {"candidate": projection.candidate(candidate), "observation": projection.observation(obs)}
        self.assertNotIn("SECRET", json.dumps(safe))
        self.assertEqual(safe["candidate"]["description"], safe["observation"]["elements"][0]["name"])
        self.assertEqual(safe["candidate"]["target"]["element"], safe["observation"]["elements"][0]["id"])
        self.assertIsNone(safe["observation"]["elements"][0]["focused"])
        t.end_command("cancelled")
        self.assertIsNot(t.projection, projection)
        t.begin_command(identifier(), "SECOND")
        self.assertEqual(t.projection.aliases.get("t", "SECOND"), "t0")

    def test_payload_binding_uses_local_slots_not_durable_content(self):
        projection = Projection()
        slots = projection.slots((SimpleNamespace(id="text-1", text="SECRET_PAYLOAD", source="user", purpose="private message"),))
        candidate = SimpleNamespace(id="type", description="type text", tool="type_text", arguments={"text": "SECRET_PAYLOAD"}, steps=())
        captured = projection.candidate(candidate)
        self.assertEqual(captured["target"]["payload_slot"], slots[0]["slot_id"])
        self.assertNotIn("SECRET_PAYLOAD", json.dumps({"slots": slots, "candidate": captured}))
        projection.slots((SimpleNamespace(id="text-1", text="same"), SimpleNamespace(id="text-2", text="same")))
        candidate.arguments = {"text": "same"}
        captured = projection.candidate(candidate)
        self.assertIsNone(captured["target"]["payload_slot"])
        self.assertTrue(captured["target"]["payload_binding_ambiguous"])

    def test_negated_submission_and_quoted_payload(self):
        self.assertFalse(intent('Type "send this" but do not send')["submit_requested"])
        self.assertIsNone(intent('Type "send this"')["submit_requested"])
        self.assertTrue(intent('Send the message')["submit_requested"])

    def test_clean_shutdown_seals_private_store(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "capture"
            t = Telemetry(TelemetryConfig(mode="training", training_consent=True, directory=root, flush_seconds=2))
            t.start()
            t.begin_command(identifier(), "SECRET")
            t.end_command("completed")
            self.assertTrue(t.close())
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / "traces.sqlite3").stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(root / "traces.sqlite3") as db:
                self.assertEqual(db.execute("SELECT sealed,drops FROM sessions").fetchone(), (1, 0))
                self.assertNotIn("SECRET", str(db.execute("SELECT body FROM events").fetchall()))

    def test_drop_or_incomplete_shutdown_cannot_seal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "capture"
            t = Telemetry(TelemetryConfig(mode="operational", directory=root, queue_size=1, flush_seconds=2))
            t.begin_command(identifier(), "secret")
            t.cancel_requested()
            t.start()
            self.assertFalse(t.close())
            with sqlite3.connect(root / "traces.sqlite3") as db:
                sealed, drops = db.execute("SELECT sealed,drops FROM sessions").fetchone()
                self.assertEqual(sealed, 0)
                self.assertGreater(drops, 0)

    def test_disk_permission_failure_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "broad"
            root.mkdir(mode=0o755)
            t = Telemetry(TelemetryConfig(mode="operational", directory=root, flush_seconds=2))
            t.start()
            t.begin_command(identifier(), "secret")
            self.assertFalse(t.close())
            self.assertTrue(t.status()["writer_failed"])
            self.assertFalse((root / "traces.sqlite3").exists())

    def test_thread_creation_failure_isolated(self):
        t = Telemetry(TelemetryConfig(mode="operational"))
        with patch("threading.Thread.start", side_effect=RuntimeError("SECRET")):
            t.start()
        self.assertFalse(t.enabled)
        self.assertTrue(t.status()["writer_failed"])

    def test_symlink_root_rejected_without_creating_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            t = Telemetry(TelemetryConfig(mode="operational", directory=link / "capture", flush_seconds=2))
            t.start()
            self.assertFalse(t.close())
            self.assertFalse((real / "capture").exists())

    def test_resident_session_retention_expires_old_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = sqlite3.connect(root / "traces.sqlite3")
            try:
                db.executescript(SQL)
                t = Telemetry(TelemetryConfig(mode="operational", directory=root, retention_days=1))
                cid = identifier()
                old = time.time() - 3 * 86400
                db.execute("INSERT INTO sessions VALUES(?,?,0,0)", (t.session_id, old))
                db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (identifier(), t.session_id, cid, None, 1, "command_ended", old, "{}"))
                db.commit()
                t._prune(db, root)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
            finally:
                db.close()

    def test_episode_deletion_also_removes_prior_stt_event(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "capture"
            t = Telemetry(TelemetryConfig(mode="training", training_consent=True, directory=root, flush_seconds=2))
            t.start()
            cid, uid = identifier(), identifier()
            t.voice_timing(uid, 42, status="transcribed")
            t.begin_command(cid, "synthetic", source="voice", utterance_id=uid)
            t.end_command("completed")
            self.assertTrue(t.close())
            with open_store(root) as db:
                delete_command(db, root, cid)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)


class LabelTests(unittest.TestCase):
    def test_delayed_typing_pending_then_verified_without_content(self):
        before = verify_typing(expected="SECRET", actual="", correct_target=True, submit_requested=False, submissions_delta=0)
        after = verify_typing(expected="SECRET", actual="SECRET", correct_target=True, submit_requested=False, submissions_delta=0, settled=True)
        self.assertEqual(before.status, "pending")
        self.assertEqual(after.status, "verified_success")
        self.assertNotIn("SECRET", str(asdict(after)))

    def test_unrequested_or_duplicate_submission_fails(self):
        for requested, count in ((False, 1), (True, 2)):
            evidence = verify_submission(requested=requested, correct_conversation=True,
                                         matching_new_messages=count, total_new_user_messages=count, settled=True)
            self.assertEqual(evidence.status, "verified_failure")

    def test_unknown_target_or_popup_change_not_success(self):
        self.assertEqual(verify_focus(intended_control_focused=None, settled=True).status, "unknown")
        self.assertEqual(verify_task({}), "unknown")
        self.assertEqual(verify_existing_tab(intended_tab_active=True, tab_count_delta=1, settled=True).status, "verified_failure")

    def test_candidate_omission_not_choice_error(self):
        self.assertEqual(classify_candidate_failure({"correct"}, {"wrong"}, {"wrong"}), "candidate_generation")
        self.assertEqual(classify_candidate_failure({"correct"}, {"correct", "wrong"}, {"wrong"}), "shortlisting")
        self.assertEqual(classify_candidate_failure({"correct"}, {"correct"}, {"correct"}), "choice")

    def test_cancel_is_not_correction(self):
        t = Telemetry(TelemetryConfig(mode="training", training_consent=True))
        t.cancel_requested()
        self.assertIs(drain(t)[0]["data"]["correction"], False)

    def test_teacher_is_not_an_independent_label(self):
        with self.assertRaises(ValueError):
            ChoiceLabel("jev", ("c1",), (identifier(),)).validate()

    def test_labels_append_and_retract_instead_of_overwrite(self):
        request, label = fixture()
        did = identifier()
        with sqlite3.connect(":memory:") as db:
            db.executescript(SQL)
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (identifier(), identifier(), identifier(), did, 1, "decision_requested", time.time(), json.dumps({"data": request})))
            first = append_label(db, did, label)
            second = append_label(db, did, ChoiceLabel(**{**asdict(label), "status": "retracted"}))
            self.assertNotEqual(first, second)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 2)


class ExportTests(unittest.TestCase):
    def test_gold_aligned_to_original_order_not_sorted_ids(self):
        request, label = fixture()
        row = training_row(request, asdict(label), complete=True, session_sealed=True)
        self.assertEqual(row["gold"]["driver_action"]["probabilities"], {"a0": 0.0, "a1": 1.0})
        self.assertEqual(set(row), {"state", "questions", "gold"})

    def test_fail_closed_matrix(self):
        changes = [
            ("provenance", "jev_derived", True), ("provenance", "teacher_involved", None),
            ("provenance", "provider", "jev"), ("provenance", "collection_mode", "operational"),
            ("token_audit", "status", "unavailable"), ("token_audit", "marker_count", 1),
            ("token_audit", "option_order", ["a1", "a0"]),
            ("token_audit", "option_tokens_retained", {"a0": 0, "a1": 3}),
            ("model_context", "checkpoint_revision", None),
            ("candidate_context", "shown_order", ["a1", "a0"]),
            ("candidate_context", "alias_to_local_candidate", {"a0": "c3", "a1": "c3"})]
        for outer, key, value in changes:
            with self.subTest(outer=outer, key=key):
                request, label = fixture()
                request[outer][key] = value
                with self.assertRaises(Ineligible):
                    training_row(request, asdict(label), complete=True, session_sealed=True)
        for key, value in (("input_representation", "sanitized_reconstruction"), ("capture_truncated", True), ("redaction_status", "failed")):
            request, label = fixture()
            request[key] = value
            with self.assertRaises(Ineligible):
                training_row(request, asdict(label), complete=True, session_sealed=True)

    def test_crash_and_incomplete_trace_rejected(self):
        request, label = fixture()
        for complete, sealed in ((False, True), (True, False), (False, False)):
            with self.assertRaises(Ineligible):
                training_row(request, asdict(label), complete=complete, session_sealed=sealed)

    def test_future_and_jev_history_leakage_rejected(self):
        for key, value in (("gold", {"a0": 1}), ("after_observation", {}), ("history", [{"source": "jev", "selected": "a0"}])):
            request, label = fixture()
            request["model_input"]["state"][key] = value
            with self.assertRaises(Ineligible):
                training_row(request, asdict(label), complete=True, session_sealed=True)

    def test_unsafe_raw_text_cannot_be_smuggled_through_input(self):
        request, label = fixture()
        request["model_input"]["state"]["observation"]["app"] = "SECRET_PRIVATE_APP"
        with self.assertRaises(Ineligible):
            training_row(request, asdict(label), complete=True, session_sealed=True)

    def test_private_words_cannot_masquerade_as_action_family(self):
        request, label = fixture()
        request["model_input"]["questions"]["driver_action"]["criteria"]["a0"] = "private_secret t1"
        with self.assertRaises(Ineligible):
            training_row(request, asdict(label), complete=True, session_sealed=True)

    def test_ambiguous_missing_or_unreviewed_gold_rejected(self):
        for update in ({"acceptable_ids": ["c3", "c7"]}, {"acceptable_ids": ["c99"]}, {"input_reviewed": False}, {"training_rights_reviewed": False}, {"status": "retracted"}, {"correction_scope": "changed_intention"}):
            request, label = fixture()
            with self.assertRaises(Ineligible):
                training_row(request, {**asdict(label), **update}, complete=True, session_sealed=True)

    def test_invalid_soft_targets_rejected_not_uniform_fallback(self):
        for weights in ({"c3": 0, "c7": 0}, {"c3": float("nan"), "c7": 0}, {"c3": 1.1, "c7": -0.1}, {"c3": 0.5}):
            request, label = fixture()
            with self.assertRaises(Ineligible):
                training_row(request, {**asdict(label), "target_distribution": weights, "preference_reviewed": True}, complete=True, session_sealed=True)

    def test_reviewed_soft_target_allowed(self):
        request, label = fixture()
        row = training_row(request, {**asdict(label), "acceptable_ids": ["c3", "c7"], "target_distribution": {"c3": 0.8, "c7": 0.2}, "preference_reviewed": True}, complete=True, session_sealed=True)
        self.assertEqual(row["gold"]["driver_action"]["probabilities"], {"a0": 0.2, "a1": 0.8})

    def test_export_and_deletion_remove_derived_files_and_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "capture"
            t = Telemetry(TelemetryConfig(mode="training", training_consent=True, directory=root, flush_seconds=2))
            t.start()
            cid, did = identifier(), identifier()
            request, label = fixture()
            t.begin_command(cid, "synthetic")
            t._record("decision_requested", request, did)
            t._record("decision_outcome", {"step_verified": "unknown"}, did)
            t.end_command("completed")
            self.assertTrue(t.close())
            with open_store(root) as db:
                append_label(db, did, label)
            exported = export_store(root, json_strings=True)
            self.assertEqual(exported["exported"], 1)
            path = Path(exported["files"][0])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIsInstance(json.loads(path.read_text())["state"], str)
            with open_store(root) as db:
                delete_command(db, root, cid)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
            self.assertTrue(all(not Path(p).exists() for p in exported["files"]))


if __name__ == "__main__":
    unittest.main()
