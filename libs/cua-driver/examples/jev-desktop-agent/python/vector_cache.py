"""Persistent embedding cache and per-window membership, with no action handles.

The cache is an optimisation, never an observation source. A lookup is always
restricted to descriptions supplied by the current observation. Disappearing
controls lose their active membership; their reusable vectors can remain cached.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Sequence


def text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class VectorCache:
    def __init__(self, path: str | Path, *, max_entries: int = 32768, max_scopes: int = 256):
        self.path = Path(path).expanduser()
        self.max_entries = max(1, int(max_entries))
        self.max_scopes = max(1, int(max_scopes))
        self._lock = threading.RLock()
        self._closed = False
        self._revision = 0
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        # A single serialised connection works with Porter's worker threads.
        self._connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS embeddings (
                model_key TEXT NOT NULL,
                text_key TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                checksum TEXT NOT NULL,
                used_ns INTEGER NOT NULL,
                PRIMARY KEY (model_key, text_key)
            );
            CREATE INDEX IF NOT EXISTS embeddings_used ON embeddings(used_ns);
            CREATE TABLE IF NOT EXISTS scopes (
                model_key TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                generation INTEGER NOT NULL,
                complete INTEGER NOT NULL,
                used_ns INTEGER NOT NULL,
                PRIMARY KEY (model_key, scope_key)
            );
            CREATE TABLE IF NOT EXISTS members (
                model_key TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                text_key TEXT NOT NULL,
                generation INTEGER NOT NULL,
                PRIMARY KEY (model_key, scope_key, text_key),
                FOREIGN KEY (model_key, scope_key) REFERENCES scopes(model_key, scope_key)
                    ON DELETE CASCADE
            );
        """)

    def get(self, model: str, texts: Sequence[str], dimensions: int) -> dict:
        import numpy as np
        keyed = {text_key(text): text for text in texts}
        found, bad = {}, []
        now = time.time_ns()
        with self._lock, self._connection:
            keys = list(keyed)
            for offset in range(0, len(keys), 256):
                chunk = keys[offset:offset + 256]
                placeholders = ",".join("?" for _ in chunk)
                rows = self._connection.execute(
                    f"SELECT text_key, dimensions, vector, checksum FROM embeddings WHERE model_key=? AND text_key IN ({placeholders})",
                    (model, *chunk),
                )
                for key, dim, blob, checksum in rows:
                    if dim != dimensions or len(blob) != dimensions * 4 or hashlib.sha256(blob).hexdigest() != checksum:
                        bad.append(key)
                        continue
                    vector = np.frombuffer(blob, dtype="<f4").copy()
                    if not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-3):
                        bad.append(key)
                        continue
                    found[keyed[key]] = vector
            self._connection.executemany(
                "UPDATE embeddings SET used_ns=? WHERE model_key=? AND text_key=?",
                ((now, model, text_key(text)) for text in found),
            )
            self._connection.executemany(
                "DELETE FROM embeddings WHERE model_key=? AND text_key=?", ((model, key) for key in bad),
            )
            if bad:
                self._revision += 1
        return found

    def put(self, model: str, vectors: dict) -> None:
        import numpy as np
        now, rows = time.time_ns(), []
        for text, value in vectors.items():
            vector = np.asarray(value, dtype="<f4")
            if vector.ndim != 1 or not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-3):
                raise ValueError("Only finite, normalised one-dimensional embeddings may be cached")
            blob = vector.tobytes()
            rows.append((model, text_key(text), len(vector), blob, hashlib.sha256(blob).hexdigest(), now))
        with self._lock, self._connection:
            self._connection.executemany("""
                INSERT INTO embeddings VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_key, text_key) DO UPDATE SET
                    dimensions=excluded.dimensions, vector=excluded.vector,
                    checksum=excluded.checksum, used_ns=excluded.used_ns
            """, rows)
            self._connection.execute("""
                DELETE FROM embeddings WHERE (model_key, text_key) IN (
                    SELECT model_key, text_key FROM embeddings
                    ORDER BY used_ns DESC, model_key, text_key LIMIT -1 OFFSET ?
                )
            """, (self.max_entries,))
            if rows:
                self._revision += 1

    def sync(self, model: str, scope: str, texts: Sequence[str], *, complete: bool) -> dict:
        keys, now = {text_key(text) for text in texts}, time.time_ns()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT generation FROM scopes WHERE model_key=? AND scope_key=?", (model, scope),
            ).fetchone()
            generation = (row[0] if row else 0) + 1
            previous = {r[0] for r in self._connection.execute(
                "SELECT text_key FROM members WHERE model_key=? AND scope_key=?", (model, scope),
            )}
            self._connection.execute("""
                INSERT INTO scopes VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(model_key, scope_key) DO UPDATE SET
                    generation=excluded.generation, complete=excluded.complete, used_ns=excluded.used_ns
            """, (model, scope, generation, int(complete), now))
            if complete:
                self._connection.execute("DELETE FROM members WHERE model_key=? AND scope_key=?", (model, scope))
            self._connection.executemany("""
                INSERT INTO members VALUES (?, ?, ?, ?)
                ON CONFLICT(model_key, scope_key, text_key) DO UPDATE SET generation=excluded.generation
            """, ((model, scope, key, generation) for key in keys))
            if not complete:
                # Keep one previous incomplete capture for reconciliation,
                # without accumulating every partial tree forever.
                self._connection.execute(
                    "DELETE FROM members WHERE model_key=? AND scope_key=? AND generation<?",
                    (model, scope, generation - 1),
                )
            # Historical rows from incomplete captures are never current.
            self._connection.execute("""
                DELETE FROM scopes WHERE (model_key, scope_key) IN (
                    SELECT model_key, scope_key FROM scopes
                    ORDER BY used_ns DESC, model_key, scope_key LIMIT -1 OFFSET ?
                )
            """, (self.max_scopes,))
            self._revision += 1
        return {"present": len(keys), "added": len(keys - previous),
                "missing": len(previous - keys) if complete else 0,
                "complete": complete, "generation": generation}

    def ensure(self, model: str, vectors: dict) -> int:
        """Repair missing rows from known vectors, without re-encoding them."""
        keys = {text_key(text): text for text in vectors}
        present, now = set(), time.time_ns()
        with self._lock, self._connection:
            ordered = list(keys)
            for offset in range(0, len(ordered), 256):
                chunk = ordered[offset:offset + 256]
                placeholders = ",".join("?" for _ in chunk)
                for key, dimensions, length in self._connection.execute(
                    f"SELECT text_key, dimensions, length(vector) FROM embeddings WHERE model_key=? AND text_key IN ({placeholders})", (model, *chunk),
                ):
                    if dimensions == len(vectors[keys[key]]) and length == dimensions * 4:
                        present.add(key)
            self._connection.executemany(
                "UPDATE embeddings SET used_ns=? WHERE model_key=? AND text_key=?",
                ((now, model, key) for key in present),
            )
            missing = {text: vectors[text] for key, text in keys.items() if key not in present}
            if missing:
                self.put(model, missing)
        return len(missing)

    def current_keys(self, model: str, scope: str) -> set[str]:
        with self._lock:
            return {r[0] for r in self._connection.execute("""
                SELECT m.text_key FROM members m JOIN scopes s
                    ON m.model_key=s.model_key AND m.scope_key=s.scope_key
                WHERE m.model_key=? AND m.scope_key=? AND m.generation=s.generation
            """, (model, scope))}

    def revision(self) -> tuple[int, int]:
        """Detect both this connection's mutations and other process writes."""
        with self._lock:
            external = self._connection.execute("PRAGMA data_version").fetchone()[0]
            return self._revision, external

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True
