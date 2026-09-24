"""Opt-in, local-only telemetry. No observer performs disk I/O on the control loop.

Only the typed projection methods below accept application objects. Never pass an
observation.compact(), provider payload, exception string, audio, or image here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import re
import sqlite3
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "porter.telemetry.v1"
FORMATTER = "porter-structural-v1"
REDACTION = "allowlist-alias-v1"
CHOICE_INSTRUCTIONS = "Select one supplied next action. Respect the complete command and do not submit unless requested."
MODES = {"off", "operational", "training"}
STATUSES = frozenset("completed cancelled failed refused partial blocked dry_run abstained budget_exhausted needs_confirmation needs_foreground unknown".split())
OUTCOMES = STATUSES | frozenset("executed executed_no_change cancelled_after_action session_revived session_ended policy_denied paged apps_discovered apps_unavailable text_prepared text_unavailable inspected inspect_unavailable inspect_no_regions target_selected switched launched launch_failed reobserve done".split())
TOOLS = frozenset("click double_click right_click type_text keypress scroll drag move_mouse browser_click browser_type browser_pointer browser_navigate browser_download browser_set_input_files browser_keypress browser_scroll bring_to_front ensure_app list_apps revive_session".split())
ROLES = frozenset("button edit entry textbox textarea searchfield combobox textfield textentry tab tabitem pagetab window menu menuitem link checkbox radio radiobutton list listitem document toolbar scrollbar text label group unknown".split())
SOURCES = frozenset("semantic accessibility browser visual vision session terminal keyboard bundle unknown".split())


def identifier() -> str:
    return uuid.uuid4().hex


def enum(value: object, allowed: set | frozenset, default: str = "unknown") -> str:
    return value if isinstance(value, str) and value in allowed else default


def number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def private_directory(path: Path) -> Path:
    path = path.expanduser().absolute()
    # Do not chmod a user's existing broad/shared directory. Fail closed instead.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("telemetry directory must not contain symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("telemetry directory must be private and owner-controlled")
    # Refuse symlinked ancestors, including a symlink used as the final directory.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("telemetry directory must not contain symlinks")
    return path


def private_file(path: Path) -> None:
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError("telemetry file must be a private regular file")
    finally:
        os.close(fd)


@dataclass(frozen=True)
class TelemetryConfig:
    mode: str = "off"
    directory: Path | None = None
    training_consent: bool = False
    queue_size: int = 128
    max_event_bytes: int = 262144
    max_store_bytes: int = 64 * 1024 * 1024
    retention_days: int = 14
    flush_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError("invalid telemetry collection mode")
        if self.mode == "training" and not self.training_consent:
            raise ValueError("training capture requires explicit consent")
        if not 1 <= self.queue_size <= 4096 or not 1024 <= self.max_event_bytes <= 1048576:
            raise ValueError("invalid telemetry queue/event budget")
        if not 1048576 <= self.max_store_bytes <= 1073741824 or not 1 <= self.retention_days <= 365:
            raise ValueError("invalid telemetry retention budget")
        if not 0 <= self.flush_seconds <= 5:
            raise ValueError("invalid telemetry flush budget")

    @classmethod
    def from_env(cls) -> "TelemetryConfig":
        mode = os.getenv("PORTER_TELEMETRY_MODE", "off").strip().lower()
        directory = os.getenv("PORTER_TELEMETRY_DIR")
        return cls(mode=mode, directory=Path(directory) if directory else None,
                   training_consent=os.getenv("PORTER_TELEMETRY_TRAINING_CONSENT") == "1")

    def path(self) -> Path:
        root = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local/state")
        return self.directory or root / "porter" / "telemetry"


class Aliases:
    """Episode-local, non-hashed aliases. The reverse map never reaches storage."""
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def get(self, kind: str, value: object) -> str | None:
        if value is None:
            return None
        text = str(value)
        if len(text) > 65536:
            raise ValueError("telemetry alias input budget exhausted")
        key = (kind, text)
        if key not in self._values:
            if len(self._values) >= 16384:
                raise ValueError("telemetry alias budget exhausted")
            self._values[key] = f"{kind}{len(self._values)}"
        return self._values[key]


def intent(goal: str, slots: tuple = ()) -> dict[str, Any]:
    # Payload phrases are not instructions. Remove them before classifying intent.
    text = goal
    for slot in slots:
        payload = getattr(slot, "text", None)
        if isinstance(payload, str) and payload:
            text = text.replace(payload, "<payload>")
    text = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"|\u201c[^\u201d]*\u201d', "<payload>", text).casefold()
    no_send = bool(re.search(r"\b(?:do not|don't|dont|without|never)\s+(?:send|submit|post)\b", text))
    send = bool(re.search(r"\b(?:send|submit|post)\b", text))
    return {"submit_requested": False if no_send else True if send else None,
            "use_existing_tab": True if "existing" in text and "tab" in text else None,
            "append_requested": True if re.search(r"\bappend\b", text) else None,
            "replace_requested": True if re.search(r"\b(?:replace|rewrite)\b", text) else None,
            "typing_requested": True if re.search(r"\b(?:type|write|enter|dictate)\b", text) else None,
            "interpretation": "heuristic_not_ground_truth"}


class Projection:
    def __init__(self) -> None:
        self.aliases = Aliases()
        self._payload_slots: dict[str, list[str]] = {}

    def slots(self, slots: tuple) -> list[dict]:
        self._payload_slots = {}
        result = []
        for slot in slots[:128]:
            sid = self.aliases.get("p", slot.id)
            payload = getattr(slot, "text", None)
            if isinstance(payload, str) and payload:
                self._payload_slots.setdefault(payload, []).append(sid)
            result.append({"slot_id": sid,
                           "purpose": self.aliases.get("t", getattr(slot, "purpose", None)),
                           "origin": enum(getattr(slot, "source", None), {"user", "user-search", "user-rename", "writer", "openrouter", "composer"})})
        return result

    def observation(self, observation: Any) -> dict[str, Any]:
        a = self.aliases.get
        elements = tuple(getattr(observation, "elements", ()))
        out = []
        for element in elements[:192]:
            role = re.sub("[^a-z]", "", str(getattr(element, "role", "")).casefold())
            if role.startswith("ax"):
                role = role[2:]
            row = {"id": a("e", getattr(element, "index", None)),
                   "name": a("t", getattr(element, "label", None)),
                   "role": enum(role, ROLES),
                   "source": enum(getattr(element, "source", None), SOURCES)}
            for key in ("focused", "visible", "enabled", "selected", "checked", "expanded"):
                row[key] = flag(getattr(element, key, None))
            # Values, content lengths, paths, URLs, screenshot IDs and bytes are omitted.
            out.append(row)
        return {"snapshot_id": a("s", getattr(observation, "snapshot_id", None)),
                "window": a("w", (getattr(observation, "pid", None), getattr(observation, "window_id", None))),
                "app": a("t", getattr(observation, "app", None)),
                "tab": a("b", getattr(observation, "browser_tab_id", None)),
                "elements": out,
                "degraded": flag(getattr(observation, "degraded", None)),
                "truncated": flag(getattr(observation, "truncated", None)),
                "omitted_elements": max(0, len(elements) - len(out)),
                "visual_regions_present": bool(getattr(observation, "visual_regions", ())),
                "read_error_present": bool(getattr(observation, "screenshot_error", None))}

    def candidate(self, candidate: Any, depth: int = 0) -> dict[str, Any]:
        a = self.aliases.get
        args = getattr(candidate, "arguments", {})
        tool = enum(getattr(candidate, "tool", None), TOOLS, "session_or_unknown")
        target = {"element": a("e", args.get("element_index")),
                  "browser_ref": a("r", args.get("ref")),
                  "window": a("w", (args.get("pid"), args.get("window_id"))) if "pid" in args else None,
                  "payload_slot": a("p", args.get("text_slot_id", args.get("slot_id")))}
        # Bound typing payloads are matched only in memory. Never infer an
        # arbitrary slot when equal payloads belong to multiple prepared slots.
        payload = args.get("text")
        matches = self._payload_slots.get(payload, []) if isinstance(payload, str) else []
        if target["payload_slot"] is None and len(matches) == 1:
            target["payload_slot"] = matches[0]
        target["payload_binding_ambiguous"] = len(matches) > 1
        children = tuple(getattr(candidate, "steps", ()))
        return {"local_id": a("c", candidate.id), "description": a("t", candidate.description),
                "tool": tool, "source": enum(getattr(candidate, "source", None), SOURCES),
                "risk": enum(getattr(candidate, "risk", None), {"safe", "confirm", "deny"}),
                "snapshot_id": a("s", getattr(candidate, "snapshot_id", None)),
                "target": target,
                "children": [self.candidate(c, depth + 1) for c in children[:32]] if depth < 4 else [],
                "children_omitted": len(children) if depth >= 4 else max(0, len(children) - 32)}


SQL = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, started REAL NOT NULL, sealed INTEGER NOT NULL DEFAULT 0, drops INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, command_id TEXT, decision_id TEXT, sequence INTEGER NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_command ON events(command_id, sequence);
CREATE INDEX IF NOT EXISTS events_decision ON events(decision_id, sequence);
CREATE TABLE IF NOT EXISTS labels(id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, created REAL NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY, filename TEXT NOT NULL, commands TEXT NOT NULL);
"""


class Telemetry:
    def __init__(self, config: TelemetryConfig | None = None) -> None:
        self.config = config or TelemetryConfig()
        self.session_id = identifier()
        self.command_id: str | None = None
        self.projection = Projection()
        self._queue: queue.Queue = queue.Queue(self.config.queue_size)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._deadline = float("inf")
        self._sequence = 0
        self._drops = 0
        self._failed = False
        self._closed = False
        self._command_started = 0.0
        self._guard = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.mode != "off" and not self._closed and not self._failed

    @property
    def training(self) -> bool:
        return self.enabled and self.config.mode == "training"

    def status(self) -> dict[str, Any]:
        return {"mode": self.config.mode, "dropped_events": self._drops,
                "writer_failed": self._failed, "queued_events": self._queue.qsize(),
                "running": bool(self._thread and self._thread.is_alive())}

    def start(self) -> None:
        if self.enabled and self._thread is None:
            self._thread = threading.Thread(target=self._write, name="porter-telemetry", daemon=True)
            try:
                self._thread.start()
            except Exception:
                self._thread = None
                self._failed = True
                self.invalidate()

    def invalidate(self) -> None:
        # A missing hook/projection is data loss, never a reason to stop control.
        with self._guard:
            self._drops += 1

    def _record(self, kind: str, body: dict, decision_id: str | None = None) -> str | None:
        """Internal only: body must already be an allowlisted, content-free projection."""
        if not self.enabled:
            return None
        try:
            with self._guard:
                self._sequence += 1
                event_id = identifier()
                payload = {"schema_version": SCHEMA, "event_id": event_id, "event_kind": kind,
                           "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                           "monotonic_ns": time.monotonic_ns(), "session_id": self.session_id,
                           "command_id": self.command_id, "decision_id": decision_id,
                           "sequence": self._sequence, "collection_mode": self.config.mode,
                           "redaction_version": REDACTION, "serializer_version": FORMATTER,
                           "porter_commit": None, "driver_revision": None, "data": body}
                raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
                if len(raw.encode()) > self.config.max_event_bytes:
                    self._drops += 1
                    return None
                self._queue.put_nowait((event_id, self.command_id, decision_id, self._sequence, kind, time.time(), raw))
                return event_id
        except Exception:
            self.invalidate()
            return None

    def begin_command(self, command_id: str, goal: str, *, source: str = "unknown", utterance_id: str | None = None) -> None:
        self.command_id = command_id if re.fullmatch(r"[a-f0-9]{32}", command_id) else identifier()
        self.projection = Projection()
        self._command_started = time.monotonic()
        body: dict[str, Any] = {"input_source": enum(source, {"text", "voice", "api", "demonstration"}),
                                "task_verified": "unknown", "stt_quality": None}
        if self.training:
            body.update(goal_alias=self.projection.aliases.get("t", goal),
                        utterance_id=utterance_id if isinstance(utterance_id, str) and re.fullmatch(r"[a-f0-9]{32}", utterance_id) else None)
        self._record("command_started", body)

    def end_command(self, status: str) -> None:
        self._record("command_ended", {"status": enum(status, STATUSES), "task_verified": "unknown",
                                      "latency_ms": (time.monotonic() - self._command_started) * 1000})
        self.command_id = None
        self.projection = Projection()

    def cancel_requested(self) -> None:
        self._record("cancel_requested", {"correction": False, "failure_category": "policy_cancellation"})

    def voice_timing(self, utterance_id: str, elapsed_ms: float, *, status: str, model: str | None = None, language: str | None = None) -> None:
        # IDs are random correlation values, never derived from the transcript.
        uid = utterance_id if re.fullmatch(r"[a-f0-9]{32}", utterance_id) else None
        body = {"utterance_id": uid, "latency_ms": number(elapsed_ms),
                "status": enum(status, {"transcribed", "failed", "cancelled"}),
                "stt_backend": "openrouter", "stt_revision": None, "stt_quality": None}
        if self.training:
            body["model_alias"] = self.projection.aliases.get("m", model)
            body["language_alias"] = self.projection.aliases.get("t", language)
        self._record("voice_transcription", body)

    def close(self) -> bool:
        self._closed = True
        self._deadline = time.monotonic() + self.config.flush_seconds
        self._stop.set()
        if self._thread is None:
            return True
        self._thread.join(self.config.flush_seconds)
        return not self._thread.is_alive() and not self._failed and not self._drops

    def _write(self) -> None:
        lock = None
        db = None
        try:
            import fcntl
            root = private_directory(self.config.path())
            private_file(root / "writer.lock")
            lock = (root / "writer.lock").open("r+")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            private_file(root / "traces.sqlite3")
            db = sqlite3.connect(root / "traces.sqlite3", timeout=0.1)
            # DELETE journal bounds disk growth; all commits happen on this worker.
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA secure_delete=ON")
            db.executescript(SQL)
            db.execute("INSERT OR REPLACE INTO settings VALUES('max_store_bytes', ?)", (str(self.config.max_store_bytes),))
            versions = source_versions()
            db.execute("INSERT INTO sessions(id, started) VALUES(?, ?)", (self.session_id, time.time()))
            db.commit()
            self._prune(db, root)
            while not self._stop.is_set() or not self._queue.empty():
                if self._stop.is_set() and time.monotonic() >= self._deadline:
                    self.invalidate()
                    break
                try:
                    event = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                eid, cid, did, seq, kind, created, raw = event
                payload = json.loads(raw)
                payload.update(versions)
                raw = json.dumps(payload, separators=(",", ":"), allow_nan=False)
                db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (eid, self.session_id, cid, did, seq, kind, created, raw))
                db.commit()
                self._queue.task_done()
                if kind == "command_ended" or seq % 32 == 0:
                    self._prune(db, root)
                if store_bytes(db, root) > self.config.max_store_bytes:
                    # Never grow indefinitely, even during one very long command.
                    self.invalidate()
                    self._failed = True
                    break
            if self._stop.is_set() and self._queue.empty() and not self._drops and time.monotonic() <= self._deadline:
                db.execute("UPDATE sessions SET sealed=1 WHERE id=?", (self.session_id,))
            db.execute("UPDATE sessions SET drops=? WHERE id=?", (self._drops, self.session_id))
            db.commit()
        except Exception:
            # Never persist repr(error): provider/SQLite exceptions can contain secrets.
            self._failed = True
            self.invalidate()
        finally:
            if db is not None:
                db.close()
            if lock is not None:
                lock.close()

    def _prune(self, db: sqlite3.Connection, root: Path) -> None:
        cutoff = time.time() - self.config.retention_days * 86400
        # Porter may remain resident for weeks. Expire completed old episodes
        # even inside the current session, but never cut the active command.
        expired = db.execute("SELECT command_id FROM events WHERE command_id IS NOT NULL GROUP BY command_id HAVING MAX(created)<?", (cutoff,)).fetchall()
        for (cid,) in expired:
            if cid != self.command_id:
                delete_command(db, root, cid)
        db.execute("DELETE FROM events WHERE command_id IS NULL AND created<?", (cutoff,))
        db.commit()
        sessions = db.execute("SELECT id FROM sessions WHERE id!=? ORDER BY started", (self.session_id,)).fetchall()
        for (sid,) in sessions:
            started = db.execute("SELECT started FROM sessions WHERE id=?", (sid,)).fetchone()[0]
            size = store_bytes(db, root)
            if started >= cutoff and size < self.config.max_store_bytes * 0.8:
                continue
            commands = [r[0] for r in db.execute("SELECT DISTINCT command_id FROM events WHERE session_id=?", (sid,)) if r[0]]
            for cid in commands:
                delete_command(db, root, cid)
            db.execute("DELETE FROM events WHERE session_id=?", (sid,))
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            db.commit()
            db.execute("VACUUM")


def source_versions() -> dict:
    """Worker-only immutable source digests; packaged/unavailable source stays null."""
    commit = os.getenv("PORTER_BUILD_COMMIT", "")
    out = {"porter_commit": commit if re.fullmatch(r"[a-fA-F0-9]{40}", commit) else None}
    for key, name in (("candidate_builder_revision", "candidates.py"), ("loop_revision", "loop.py"), ("driver_revision", "driver.py")):
        try:
            source = Path(__file__).with_name(name)
            out[key] = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() and source.stat().st_size < 2000000 else None
        except OSError:
            out[key] = None
    return out


def store_bytes(db: sqlite3.Connection, root: Path) -> int:
    size = (root / "traces.sqlite3").stat().st_size
    for (filename,) in db.execute("SELECT filename FROM exports"):
        if not re.fullmatch(r"[a-f0-9]{32}\.(?:jsonl|json)", filename):
            raise ValueError("invalid managed export filename")
        target = root / "exports" / filename
        if target.is_symlink():
            raise ValueError("managed export must not be a symlink")
        if target.exists():
            size += target.stat().st_size
    return size



def delete_command(db: sqlite3.Connection, root: Path, command_id: str) -> None:
    """Delete source, label revisions and all managed exports containing the episode."""
    for eid, filename, commands in db.execute("SELECT id, filename, commands FROM exports").fetchall():
        if command_id in json.loads(commands):
            if not re.fullmatch(r"[a-f0-9]{32}\.(?:jsonl|json)", filename):
                raise ValueError("invalid managed export filename")
            target = root / "exports" / filename
            if target.is_symlink():
                raise ValueError("managed export must not be a symlink")
            target.unlink(missing_ok=True)
            db.execute("DELETE FROM exports WHERE id=?", (eid,))
    # An accepted utterance was transcribed before its command ID existed.
    utterances = set()
    for (body,) in db.execute("SELECT body FROM events WHERE command_id=? AND kind='command_started'", (command_id,)):
        uid = json.loads(body).get("data", {}).get("utterance_id")
        if uid:
            utterances.add(uid)
    for eid, body in db.execute("SELECT id,body FROM events WHERE kind='voice_transcription'").fetchall():
        if json.loads(body).get("data", {}).get("utterance_id") in utterances:
            db.execute("DELETE FROM events WHERE id=?", (eid,))
    db.execute("DELETE FROM labels WHERE decision_id IN (SELECT decision_id FROM events WHERE command_id=?)", (command_id,))
    db.execute("DELETE FROM events WHERE command_id=?", (command_id,))
