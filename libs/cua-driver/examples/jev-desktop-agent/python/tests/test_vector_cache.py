"""Persistent-cache contracts without Jev, model downloads, or desktop calls."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from contracts import Candidate, Element, Observation
from retrieval import CandidateRetriever, OnnxEncoder, default_cache_path, resolve_model_dir
from vector_cache import VectorCache, text_key


class FakeSession:
    def __init__(self):
        self.calls = 0

    def run(self, outputs, inputs):
        self.calls += len(inputs["input_ids"])
        # Finite, deterministic fixture vectors; not semantic-quality evidence.
        ids = inputs["input_ids"]
        return [np.asarray([[float(row.sum()), 2, 3, 4] for row in ids], dtype=np.float32)]


class FakeTokenizer:
    def encode_batch(self, texts):
        return [SimpleNamespace(ids=[len(text) + 2, 2], attention_mask=[1, 1]) for text in texts]


class FakeEncoder(OnnxEncoder):
    def __init__(self, path, model="model-one"):
        super().__init__("unused", cache_path=path)
        self.dimensions = 4
        self.fixture_model = model

    def _load(self):
        if self.session is None:
            self.model_identity = self.fixture_model
            self.session, self.tokenizer = FakeSession(), FakeTokenizer()
            self.store = VectorCache(self.cache_path)


class VectorCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "vectors.sqlite3"
        self.cache = VectorCache(self.path)
        self.addCleanup(self.cache.close)
        self.vector = np.array([1, 0, 0, 0], dtype=np.float32)

    def test_vectors_survive_restart_without_plaintext(self):
        self.cache.put("model", {"Private UI label": self.vector})
        self.cache.close()
        cache = VectorCache(self.path)
        self.addCleanup(cache.close)
        np.testing.assert_array_equal(cache.get("model", ["Private UI label"], 4)["Private UI label"], self.vector)
        self.assertNotIn(b"Private UI label", self.path.read_bytes())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_missing_and_changed_elements_reconcile(self):
        self.cache.sync("model", "window", ["old", "same"], complete=True)
        result = self.cache.sync("model", "window", ["same", "new"], complete=True)
        self.assertEqual((result["added"], result["missing"]), (1, 1))
        self.assertEqual(self.cache.current_keys("model", "window"), {text_key("same"), text_key("new")})

    def test_partial_capture_never_reactivates_absent_element(self):
        self.cache.sync("model", "window", ["old", "same"], complete=True)
        partial = self.cache.sync("model", "window", ["same"], complete=False)
        self.assertEqual(partial["missing"], 0)
        self.assertEqual(self.cache.current_keys("model", "window"), {text_key("same")})
        result = self.cache.sync("model", "window", ["same"], complete=True)
        self.assertEqual(result["missing"], 1)
        self.cache.sync("model", "window", [], complete=True)
        self.assertFalse(self.cache.current_keys("model", "window"))

    def test_window_and_model_namespaces_are_separate(self):
        self.cache.put("model-one", {"control": self.vector})
        self.assertFalse(self.cache.get("model-two", ["control"], 4))
        self.cache.sync("model-one", "window-one", ["a"], complete=True)
        self.cache.sync("model-one", "window-two", ["b"], complete=True)
        self.assertEqual(self.cache.current_keys("model-one", "window-one"), {text_key("a")})
        self.assertFalse(self.cache.current_keys("model-two", "window-one"))

    def test_corrupt_wrong_dimensions_and_missing_rows_get_repaired(self):
        self.cache.put("model", {"a": self.vector, "b": self.vector})
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE embeddings SET vector=? WHERE text_key=?", (b"x" * 16, text_key("a")))
            connection.execute("UPDATE embeddings SET dimensions=9 WHERE text_key=?", (text_key("b"),))
        self.assertFalse(self.cache.get("model", ["a", "b"], 4))
        self.assertEqual(self.cache.ensure("model", {"a": self.vector, "b": self.vector}), 2)
        self.assertEqual(len(self.cache.get("model", ["a", "b"], 4)), 2)

    def test_cache_is_bounded(self):
        cache = VectorCache(Path(self.temp.name) / "bounded.sqlite3", max_entries=2, max_scopes=1)
        self.addCleanup(cache.close)
        for text in ("a", "b", "c"):
            cache.put("model", {text: self.vector})
        self.assertEqual(len(cache.get("model", ["a", "b", "c"], 4)), 2)
        cache.sync("model", "one", ["a"], complete=True)
        cache.sync("model", "two", ["b"], complete=True)
        self.assertFalse(cache.current_keys("model", "one"))

    def test_threaded_worker_access_is_serialised(self):
        def work(i):
            self.cache.put("model", {str(i): self.vector})
            return self.cache.get("model", [str(i)], 4)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(work, range(16)))
        self.assertEqual(sum(bool(r) for r in results), 16)

    def test_invalid_vectors_are_not_persisted(self):
        for vector in ([0, 0, 0, 0], [float("nan"), 0, 0, 0], [[1, 0, 0, 0]]):
            with self.assertRaises(ValueError):
                self.cache.put("model", {"bad": vector})

    def test_encoder_restart_encodes_only_new_or_changed_descriptions(self):
        encoder = FakeEncoder(self.path)
        first = encoder.embed(["control a", "control b"])
        self.assertEqual(encoder.session.calls, 2)
        encoder.close()
        encoder = FakeEncoder(self.path)
        self.addCleanup(encoder.close)
        second = encoder.embed(["control a", "control b"])
        np.testing.assert_array_equal(first, second)
        self.assertEqual(encoder.session.calls, 0)
        self.assertEqual(encoder.statistics["database_hits"], 2)
        encoder.embed(["control a", "renamed control b"])
        self.assertEqual(encoder.session.calls, 1)
        encoder.embed_query("private request")
        with sqlite3.connect(self.path) as connection:
            keys = {r[0] for r in connection.execute("SELECT text_key FROM embeddings")}
        self.assertNotIn(text_key("private request"), keys)

    def test_retriever_sync_removes_missing_action_and_rebinds_tokens(self):
        encoder = FakeEncoder(self.path)
        self.addCleanup(encoder.close)
        retriever = CandidateRetriever(encoder)
        old = Element(1, "old-token", "button", "Open settings")
        state = Observation("s1", 1, 2, "Demo", "Demo", (old,))
        def candidate(e, snapshot):
            return Candidate("click", "Open settings", "click", {"element_index": e.index, "element_token": e.token, "snapshot_id": snapshot}, snapshot_id=snapshot)
        first = retriever.select("Open settings", [candidate(old, "s1")], observation=state)
        self.assertEqual(first[0].arguments["element_token"], "old-token")
        fresh = replace(old, token="new-token")
        second = retriever.select("Open settings", [candidate(fresh, "s2")], observation=replace(state, snapshot_id="s2", elements=(fresh,)))
        self.assertEqual(second[0].arguments["element_token"], "new-token")
        self.assertEqual(encoder.statistics["encoded"], 2)  # one card and one RAM-only query
        result = retriever.select("Open settings", [candidate(fresh, "s2")], observation=replace(state, snapshot_id="s3", elements=()))
        self.assertFalse(result)
        self.assertEqual(encoder.last_sync["missing"], 1)

    def test_live_ram_vector_repairs_deleted_database_row(self):
        encoder = FakeEncoder(self.path)
        self.addCleanup(encoder.close)
        state = Observation("s1", 1, 2, "Demo", "Demo", ())
        encoder.sync_observation(state, ["a"])
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM embeddings")
        encoder.sync_observation(state, ["a"])
        self.assertEqual(encoder.session.calls, 1)
        self.assertEqual(encoder.last_sync["repaired_vector_rows"], 1)

    def test_unchanged_descriptions_skip_database_writes(self):
        encoder = FakeEncoder(self.path)
        self.addCleanup(encoder.close)
        state = Observation("s1", 1, 2, "Demo", "Demo", ())
        encoder.sync_observation(state, ["a"])
        changes = encoder.store._connection.total_changes
        encoder.sync_observation(replace(state, snapshot_id="s2"), ["a"])
        self.assertTrue(encoder.last_sync["unchanged"])
        self.assertEqual(encoder.store._connection.total_changes, changes)
        # Another process's membership changes also invalidate the skip.
        other = VectorCache(self.path)
        self.addCleanup(other.close)
        scope = next(iter(encoder._scope_cache))
        other.sync(encoder.model_identity, scope, ["other"], complete=True)
        encoder.sync_observation(state, ["a"])
        self.assertFalse(encoder.last_sync["unchanged"])
        self.assertEqual(encoder.store.current_keys(encoder.model_identity, scope), {text_key("a")})

    def test_portable_paths_and_explicit_missing_model(self):
        with patch.dict("os.environ", {"XDG_CACHE_HOME": self.temp.name, "PORTER_VECTOR_CACHE_PATH": ""}), patch("retrieval.sys.platform", "linux"):
            self.assertEqual(default_cache_path(), Path(self.temp.name) / "porter/retrieval/vectors-v1.sqlite3")
        with patch.dict("os.environ", {"PORTER_EMBEDDING_MODEL_DIR": ""}):
            with self.assertRaises(FileNotFoundError):
                resolve_model_dir(Path(self.temp.name) / "absent")


if __name__ == "__main__":
    unittest.main()
