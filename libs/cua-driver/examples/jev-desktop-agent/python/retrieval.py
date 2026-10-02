"""Local hybrid retrieval. Vectors cache descriptions; actions stay snapshot-bound.

No Driver or chooser calls are made here. Recognised intent supplies protected
candidate groups, rather than a hard keyword filter. Dense and fuzzy lexical
retrieval retain alternatives when the interpretation is incomplete.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from contracts import Candidate, Element, Observation, StepRecord, thaw
from writer import PreparedText
from vector_cache import VectorCache

FALLBACK = frozenset({"done", "abstain", "reobserve", "more-actions"})
EDITABLE = frozenset({"combobox", "edit", "entry", "searchfield", "textarea", "textbox", "textfield", "textentry"})
BROWSERS = frozenset({"firefox", "chrome"})
WORDS = frozenset("firefox chrome chatgpt youtube open type write close closed closing search first left right second rather than without window website selected browser enter input chat composer message address navigate exit quit shut dismiss leave keep running send unsent find text supplied provided".split())


def porter_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Porter"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Porter"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "porter"


def default_cache_path() -> Path:
    override = os.getenv("PORTER_VECTOR_CACHE_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = porter_data_dir() / "Cache"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "Porter"
    else:
        base = Path(os.getenv("XDG_CACHE_HOME") or Path.home() / ".cache") / "porter"
    return base / "retrieval" / "vectors-v1.sqlite3"


def resolve_model_dir(configured: str | Path | None = None) -> Path:
    explicit = os.getenv("PORTER_EMBEDDING_MODEL_DIR", "").strip() or configured
    path = Path(explicit).expanduser() if explicit else porter_data_dir() / "models" / "bge-m3" / "onnx"
    if not (path / "model.onnx").is_file() or not (path / "tokenizer.json").is_file():
        raise FileNotFoundError(
            f"Porter's local BGE-M3 model is missing at {path}. "
            "Install the ONNX assets there or set PORTER_EMBEDDING_MODEL_DIR."
        )
    return path


def create_retriever(*, model_dir: str | Path | None = None, cache_path: str | Path | None = None):
    cache_path = os.getenv("PORTER_VECTOR_CACHE_PATH", "").strip() or cache_path or default_cache_path()
    return CandidateRetriever(OnnxEncoder(resolve_model_dir(model_dir), cache_path=cache_path))


def role(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.casefold())


def app_identity(value: str) -> str:
    value = value.strip().casefold()
    # Inventories may use a page title as app_name; browser ownership wins.
    for name in ("firefox", "chrome"):
        if re.search(r"\b" + name + r"\b", value):
            return name
    if value == "chatgpt" or value.endswith(" — chatgpt"):
        return "chatgpt"
    return value


def one_edit(a: str, b: str) -> bool:
    """One insertion, deletion, substitution, or adjacent transposition."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        differences = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(differences) == 1 or (
            len(differences) == 2 and differences[1] == differences[0] + 1
            and a[differences[0]] == b[differences[1]]
            and a[differences[1]] == b[differences[0]]
        )
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(shorter) and shorter[i] == longer[i]:
        i += 1
    return shorter[i:] == longer[i + 1:]


def retrieval_query(goal: str, texts: Sequence[PreparedText]) -> str:
    """Mask payloads before spelling repair; never modify executable text."""
    query = goal
    for slot in sorted(texts, key=lambda x: len(x.text), reverse=True):
        if slot.text:
            query = query.replace(slot.text, "the supplied text")
            parsed = urlparse(slot.text)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                query = re.sub(re.escape(parsed.hostname), "the provided website", query, flags=re.I)
    query = re.sub(r"https?://\S+", "the provided website", query)
    # Quoted strings are literals even if the writer has not made a slot yet.
    return re.sub(r'"[^"\n]*"', "the supplied text", query)


def repair(query: str, vocabulary: set[str]) -> tuple[str, tuple[tuple[str, str], ...], dict[str, tuple[str, ...]]]:
    repairs, ambiguous = [], {}
    def change(match):
        word = match.group().casefold()
        if len(word) < 4 or word in vocabulary:
            return match.group()
        near = tuple(sorted(v for v in vocabulary if one_edit(word, v)))
        if len(near) == 1:
            repairs.append((word, near[0]))
            return near[0]
        if near:
            ambiguous[word] = near
        return match.group()
    return re.sub(r"[A-Za-z]+", change, query), tuple(repairs), ambiguous


@dataclass(frozen=True)
class Intent:
    query: str
    positive: str
    targets: tuple[str, ...]
    sites: tuple[str, ...]
    closing: bool
    typing: bool
    navigating: bool
    searching: bool
    pane: str | None
    forbidden: tuple[tuple[str, str], ...]
    ambiguous: dict[str, tuple[str, ...]]


def interpret(query: str, texts: Sequence[PreparedText], obs: Observation,
              ambiguous: dict[str, tuple[str, ...]]) -> Intent:
    positive = query.casefold().replace("’", "'")
    forbidden: list[tuple[str, str]] = []
    # Only explicit, bounded clauses are interpreted. Unknown phrasing remains
    # visible to semantic retrieval and to Jev with the original instruction.
    for app in ("firefox", "chrome", "chatgpt"):
        patterns = [
            ("close", rf"\b(?:do not|don't|without)\s+(?:close|closing|quit|quitting)\s+(?:the\s+)?(?:desktop\s+)?{app}\b"),
            ("close", rf"\b(?:leave|keep|keeping)\s+(?:the\s+)?{app}\s+(?:running|open)\b"),
            ("use", rf"\b(?:rather than|instead of)\s+(?:the\s+)?(?:desktop\s+)?{app}(?:\s+app)?\b"),
            ("use", rf"\b(?:do not|don't|without)\s+(?:use|using)\s+(?:the\s+)?(?:desktop\s+)?{app}\b"),
        ]
        for operation, pattern in patterns:
            if re.search(pattern, positive):
                forbidden.append((operation, app))
                positive = re.sub(pattern, " ", positive)
    if re.search(r"\b(?:keep|leave)\s+(?:the\s+)?browser\s+(?:open|running)\b", positive):
        forbidden.extend(("close", app) for app in BROWSERS)
        positive = re.sub(r"\b(?:keep|leave)\s+(?:the\s+)?browser\s+(?:open|running)\b", " ", positive)
    closing = bool(re.search(r"\b(?:close|closed|closing|exit|quit|shut|dismiss)\b", positive))
    explicit = [a for a in ("firefox", "chrome") if re.search(r"\b" + a + r"\b", positive)]
    sites = [a for a in ("chatgpt", "youtube") if re.search(r"\b" + a + r"\b", positive)]
    urls = [t.text for t in texts if urlparse(t.text).scheme in {"http", "https"}]
    for url in urls:
        stem = (urlparse(url).hostname or "").removeprefix("www.").split(".")[0]
        if stem and stem not in sites:
            sites.append(stem)
    current = app_identity(obs.app)
    if explicit:
        targets = explicit
    elif closing and "chatgpt" in sites:
        targets = ["chatgpt"]
    elif sites or urls or re.search(r"\b(?:browser|website|address bar|url bar)\b", positive):
        targets = [current] if current in BROWSERS else sorted(BROWSERS)
    else:
        targets = [current]
    # An ambiguous app correction adds alternatives instead of choosing one.
    for choices in ambiguous.values():
        targets.extend(a for a in choices if a in BROWSERS or a == "chatgpt")
    targets = [a for a in dict.fromkeys(targets) if ("use", a) not in forbidden]
    typing = bool(texts) or bool(re.search(r"\b(?:type|write|enter|input|message|composer|search|find|look|chat box)\b", positive))
    pane = "left" if re.search(r"\b(?:first|left|leftmost)\b", positive) else "right" if re.search(r"\b(?:second|right|rightmost)\b", positive) else None
    return Intent(query, positive, tuple(targets), tuple(sites if targets != ["chatgpt"] else ()),
                  closing, typing, bool(urls) and not closing,
                  bool(re.search(r"\b(?:search|find videos|look up)\b", positive)), pane, tuple(forbidden), ambiguous)


@dataclass(frozen=True)
class Card:
    candidate: Candidate
    text: str
    app: str
    operation: str
    element: Element | None
    document: Element | None
    group: str | None = None
    priority: int = 0


class Context:
    def __init__(self, observation: Observation):
        self.obs = observation
        self.by_index = {e.index: e for e in observation.elements}
        self.by_ref = {e.browser_ref: e for e in observation.elements if e.browser_ref}
        self.windows = {(w.get("pid"), w.get("window_id")): w for w in observation.desktop_windows}
        self._ancestors: dict[int, tuple[Element | None, Element | None]] = {}
        self.positions = {}
        fields: dict[tuple[str, str], list[Element]] = {}
        for e in observation.elements:
            document, frame = self.ancestors(e)
            if e.enabled and e.bounds and role(e.role) in EDITABLE and (document or e.source == "browser") and (not frame or frame.label == observation.window_title):
                fields.setdefault((role(e.role), e.label), []).append(e)
        for group in fields.values():
            if len(group) > 1:
                ordered = sorted(group, key=lambda e: (e.bounds.x, e.bounds.y, e.index))
                for position, e in enumerate(ordered):
                    self.positions[e.index] = f"pane {position + 1} " + ("left first" if position == 0 else "right last" if position == len(ordered) - 1 else "middle")

    def ancestors(self, element: Element | None) -> tuple[Element | None, Element | None]:
        if element is None:
            return None, None
        if element.index in self._ancestors:
            return self._ancestors[element.index]
        node, document, frame, seen = element, None, None, set()
        while node and node.index not in seen:
            seen.add(node.index)
            if document is None and role(node.role) in {"document", "documentweb"}:
                document = node
            if role(node.role) in {"frame", "window"}:
                frame = node
                break
            node = self.by_index.get(node.parent_index)
        self._ancestors[element.index] = document, frame
        return document, frame

    def element(self, candidate: Candidate) -> Element | None:
        args = candidate.arguments
        return self.by_index.get(args.get("element_index")) or self.by_ref.get(args.get("ref"))

    def fresh(self, candidate: Candidate) -> bool:
        if candidate.id in FALLBACK:
            return True
        if candidate.id.startswith("switch-window-"):
            return (candidate.arguments.get("pid"), candidate.arguments.get("window_id")) in self.windows
        for node in (candidate, *candidate.steps):
            args = node.arguments
            for snapshot in (node.snapshot_id, args.get("snapshot_id")):
                if snapshot and snapshot != self.obs.snapshot_id:
                    return False
            if node.capture_id and node.capture_id != self.obs.capture_id:
                return False
            target = args.get("target")
            if target and (target.get("pid"), target.get("window_id")) != (self.obs.pid, self.obs.window_id):
                return False
            if args.get("target_id") and args["target_id"] != self.obs.browser_target_id:
                return False
            if args.get("tab_id") and args["tab_id"] != self.obs.browser_tab_id:
                return False
            e = self.element(node)
            if ("element_index" in args or "ref" in args) and e is None:
                return False
            if e:
                if not e.enabled or (args.get("element_token") and args["element_token"] != e.token):
                    return False
                _, frame = self.ancestors(e)
                if frame and frame.label != self.obs.window_title:
                    return False
        return True

    def card(self, candidate: Candidate) -> Card:
        e = self.element(candidate)
        document, _ = self.ancestors(e)
        app = app_identity(self.obs.app)
        operation = "activate"
        if candidate.id.startswith("switch-window-"):
            window = self.windows[(candidate.arguments.get("pid"), candidate.arguments.get("window_id"))]
            app = app_identity(str(window.get("app_name") or window.get("title") or ""))
            title = str(window.get("title") or "")[:160]
            return Card(candidate, f"switch window | app {app} | page {title}", app, "switch", None, None)
        if candidate.id.startswith("launch-app-"):
            name = str(candidate.arguments.get("name") or "")
            app = app_identity(name)
            return Card(candidate, f"launch application | app {name}", app, "launch", None, None)
        nodes = (candidate, *candidate.steps)
        if any(urlparse(str(n.arguments.get("text", n.arguments.get("url", "")))).scheme in {"http", "https"} for n in nodes):
            operation = "navigate website"
        elif candidate.tool in {"type_text", "browser_type"}:
            operation = "type text"
        elif any(n.tool in {"type_text", "browser_type"} for n in nodes):
            operation = "type and submit"
        if (e and re.search(r"\b(?:close|exit|quit)\b", e.label, re.I)) or any(
            {str(k).casefold() for k in n.arguments.get("keys", ())} == {"alt", "f4"} for n in nodes
        ):
            operation = "close"
        page = (document.label if document else self.obs.browser_title if e and e.source == "browser" else "") or "browser interface"
        label = f"{e.role} {e.label}" if e else candidate.description
        label = re.sub(r"prepared text\s+\S+\s*\([^)]*\)", "supplied text", label, flags=re.I)
        text = f"{operation} | app {app} | page {page[:140]} | control {label[:180]}"
        if e and e.index in self.positions:
            text += " | " + self.positions[e.index]
        return Card(candidate, text, app, operation, e, document)


class OnnxEncoder:
    """CPU encoder with a bounded RAM LRU and optional persistent vector store."""
    def __init__(self, model_dir: str | Path, *, threads: int = 8, max_entries: int = 8192,
                 cache_path: str | Path | None = None):
        self.model_dir = Path(model_dir)
        self.threads = threads
        self.max_entries = max(64, max_entries)
        self.cache: OrderedDict[str, object] = OrderedDict()
        self.session = self.tokenizer = None
        self.model_identity = ""
        self.cache_path = cache_path
        self.store: VectorCache | None = None
        self._lock = threading.RLock()
        self.dimensions = 1024
        self.last_sync: dict = {}
        self.statistics = {"ram_hits": 0, "database_hits": 0, "encoded": 0}
        self._scope_cache: OrderedDict[str, tuple] = OrderedDict()

    def load(self):
        with self._lock:
            return self._load()

    def _load(self):
        if self.session is not None:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer
        model, tokenizer = self.model_dir / "model.onnx", self.model_dir / "tokenizer.json"
        manifest = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(self.model_dir.iterdir()) if p.is_file()]
        self.model_identity = hashlib.sha256(json.dumps({"assets": manifest, "encoder": "bge-m3-f32-l2-pad1-truncate256-v1"}, sort_keys=True).encode()).hexdigest()
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(str(tokenizer))
        tok.enable_truncation(max_length=256)
        if session.get_providers() != ["CPUExecutionProvider"]:
            raise RuntimeError("Porter retrieval must use the CPU provider")
        if "sentence_embedding" not in {output.name for output in session.get_outputs()}:
            raise ValueError("The local model does not expose sentence_embedding")
        store = VectorCache(self.cache_path) if self.cache_path is not None else None
        self.session, self.tokenizer, self.store = session, tok, store

    def embed(self, texts: Sequence[str], *, persist: bool = True):
        with self._lock:
            return self._embed(texts, persist=persist)

    def _embed(self, texts: Sequence[str], *, persist: bool):
        import numpy as np
        self.load()
        if not texts:
            return np.empty((0, self.dimensions), dtype=np.float32)
        # Keep the output valid even when this batch exceeds the LRU capacity.
        rows = {t: self.cache[t] for t in dict.fromkeys(texts) if t in self.cache}
        self.statistics["ram_hits"] += len(rows)
        pending = [t for t in dict.fromkeys(texts) if t not in rows]
        if persist and self.store is not None and pending:
            database_rows = self.store.get(self.model_identity, pending, self.dimensions)
            self.statistics["database_hits"] += len(database_rows)
            rows.update(database_rows)
            pending = [t for t in pending if t not in rows]
        generated = {}
        for offset in range(0, len(pending), 16):
            chunk = pending[offset:offset + 16]
            encoded = self.tokenizer.encode_batch(chunk)
            width = max(len(e.ids) for e in encoded)
            ids = np.ones((len(chunk), width), dtype=np.int64)
            mask = np.zeros_like(ids)
            for i, e in enumerate(encoded):
                ids[i, :len(e.ids)] = e.ids
                mask[i, :len(e.ids)] = e.attention_mask
            vectors = self.session.run(["sentence_embedding"], {"input_ids": ids, "attention_mask": mask})[0]
            if vectors.shape != (len(chunk), self.dimensions) or not np.isfinite(vectors).all():
                raise ValueError("Invalid BGE-M3 sentence embeddings")
            vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
            rows.update(zip(chunk, vectors))
            generated.update(zip(chunk, vectors))
            self.statistics["encoded"] += len(chunk)
        if persist and self.store is not None and generated:
            self.store.put(self.model_identity, generated)
        for text in dict.fromkeys(texts):
            self.cache[text] = rows[text]
            self.cache.move_to_end(text)
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)
        return np.asarray([rows[t] for t in texts], dtype=np.float32)

    def embed_query(self, query: str):
        # Keep query vectors in RAM only; the database holds UI descriptions.
        return self.embed([query], persist=False)[0]

    def sync_observation(self, observation: Observation, texts: Sequence[str]) -> None:
        with self._lock:
            if self.cache_path is None:
                return
            vectors = self.embed(texts)
            scope = hashlib.sha256(json.dumps([observation.pid, observation.window_id]).encode()).hexdigest()
            complete = not observation.truncated and not observation.degraded
            signature = (frozenset(texts), complete)
            revision = self.store.revision()
            previous = self._scope_cache.get(scope)
            if previous and previous[:2] == (signature, revision):
                self._scope_cache.move_to_end(scope)
                self.last_sync = {**previous[2], "added": 0, "missing": 0,
                                  "repaired_vector_rows": 0, "unchanged": True}
                return
            repaired = self.store.ensure(self.model_identity, dict(zip(texts, vectors)))
            self.last_sync = self.store.sync(self.model_identity, scope, texts,
                complete=complete)
            self.last_sync["repaired_vector_rows"] = repaired
            self.last_sync["unchanged"] = False
            self._scope_cache[scope] = (signature, self.store.revision(), dict(self.last_sync))
            self._scope_cache.move_to_end(scope)
            while len(self._scope_cache) > self.store.max_scopes:
                self._scope_cache.popitem(last=False)

    def close(self):
        with self._lock:
            if self.store is not None:
                self.store.close()
            self.store = self.session = self.tokenizer = None
            self.cache.clear()
            self._scope_cache.clear()


class CandidateRetriever:
    def __init__(self, encoder, *, nominal_limit: int = 10):
        self.encoder = encoder
        self.nominal_limit = max(5, nominal_limit)
        self.last_diagnostics: dict = {}

    def prepare(self, goal: str, candidates: Sequence[Candidate], observation: Observation,
                texts: Sequence[PreparedText] = (), history: Sequence[StepRecord] = ()):
        context = Context(observation)
        cards, seen = [], set()
        for c in candidates:
            key = ("launch", json.dumps(thaw(c.arguments), sort_keys=True)) if c.id.startswith("launch-app-") else ("id", c.id)
            if c.id not in FALLBACK and key not in seen and context.fresh(c):
                cards.append(context.card(c))
                seen.add(key)
        self._observed_cards = cards
        query = retrieval_query(goal, texts)
        vocabulary = set(WORDS)
        # Repair only this bounded instruction vocabulary. Arbitrary UI words
        # must never rewrite valid instructions such as exit -> edit. Control
        # name typos are handled by fuzzy ranking, without rewriting the query.
        corrected, repairs, ambiguous = repair(query, vocabulary)
        intent = interpret(corrected, texts, observation, ambiguous)
        current = app_identity(observation.app)
        documents = [e for e in observation.elements if role(e.role) in {"document", "documentweb"}]
        site_docs = {e.index for e in documents if any(s in e.label.casefold() for s in intent.sites)}
        route_cards = [c for c in cards if c.operation == "switch" and c.app in intent.targets]
        page_routes = [c for c in route_cards if any(s in c.text.casefold() for s in intent.sites)]
        routing = current not in intent.targets or (
            bool(intent.sites) and not site_docs and not intent.navigating and bool(page_routes)
        )
        # History is a hint only when the current field independently contains
        # the requested payload. Values are never put in embedding cards.
        filled_documents = set()
        if texts:
            for card in cards:
                e = card.element
                typed = any(h.executed and h.selected_id == card.candidate.id and h.outcome in {"executed", "delivered"} for h in history[-4:])
                if e and card.operation == "type text" and e.value == texts[0].text and typed and card.document:
                    filled_documents.add(card.document.index)
        primary: list[Card] = []
        for card in cards:
            c, e, doc = card.candidate, card.element, card.document
            if ("use", card.app) in intent.forbidden or (card.operation == "close" and ("close", card.app) in intent.forbidden):
                continue
            group, priority = None, 0
            if routing:
                if card in (page_routes or route_cards):
                    group, priority = f"route:{card.app}", 5
                elif card.operation == "launch" and card.app in intent.targets and not intent.closing and not route_cards:
                    group, priority = f"launch:{card.app}", 4
            elif card.app in intent.targets and card.operation not in {"switch", "launch"}:
                if intent.closing and card.operation == "close" and doc is None:
                    group, priority = f"close:{card.app}", 5
                elif intent.navigating and card.operation == "navigate website" and c.steps:
                    group, priority = f"navigate:{card.app}", 6
                elif intent.navigating and ("address bar" in c.description.casefold() or (e and doc is None and re.search(r"address|url", e.label, re.I))):
                    group, priority = f"address:{card.app}", 4
                elif not intent.closing and intent.typing and e and role(e.role) in EDITABLE:
                    page_ok = (doc is not None and (not site_docs or doc.index in site_docs)) or (e.source == "browser" and bool(observation.browser_url))
                    if not intent.navigating and page_ok:
                        group, priority = f"input:{e.index}", 5
                        # An exact fresh value plus successful typing history is a
                        # ranking hint, never a fabricated completed-task claim.
                        if doc and doc.index in filled_documents:
                            priority = 2
                elif not intent.closing and not intent.navigating and intent.searching and doc and (not site_docs or doc.index in site_docs) and e and re.fullmatch("search", e.label, re.I):
                    group, priority = f"submit:{doc.index}", 6 if doc.index in filled_documents else 2
            primary.append(Card(c, card.text, card.app, card.operation, e, doc, group, priority))
        # Spatial narrowing is a preference. Alternative panes remain in the
        # semantic pool, with their own contextual cards.
        fields = {c.element.index: c.element for c in primary if c.group and c.group.startswith("input:")}
        preferred = None
        if intent.pane and fields and all(e.bounds for e in fields.values()):
            ordered = sorted(fields.values(), key=lambda e: (e.bounds.x, e.bounds.y, e.index))
            preferred = ordered[0 if intent.pane == "left" else -1].index
        updated = []
        for card in primary:
            group, priority, text = card.group, card.priority, card.text
            if preferred is not None and card.element and card.element.index in fields and card.element.index != preferred:
                group, priority = None, 0
            updated.append(Card(card.candidate, text, card.app, card.operation, card.element, card.document, group, priority))
        return updated, intent, repairs, routing

    def select(self, goal: str, candidates: Sequence[Candidate], *, observation: Observation,
               texts: Sequence[PreparedText] = (), history: Sequence[StepRecord] = (),
               limit: int = 32, page: int = 0,
               seen_candidate_ids: frozenset[str] = frozenset()) -> list[Candidate]:
        import numpy as np
        cards, intent, repairs, routing = self.prepare(goal, candidates, observation, texts, history)
        sync = getattr(self.encoder, "sync_observation", None)
        if callable(sync):
            sync(observation, [c.text for c in self._observed_cards])
        fallback = [next(c for c in candidates if c.id == ident) for ident in ("done", "abstain", "reobserve", "more-actions") if any(c.id == ident for c in candidates)]
        ceiling = max(1, int(limit))
        # A real action always gets a slot when one exists.
        fallback_room = max(0, ceiling - bool(cards))
        if cards and ceiling == 4:
            # At the smallest supported loop budget, keep the paging route
            # ahead of optional refresh so the rest of the pool is reachable.
            fallback.sort(key=lambda c: c.id == "reobserve")
        fallback = fallback[:fallback_room]
        if not cards:
            self.last_diagnostics = {"query": intent.query, "repairs": repairs, "protected_ids": [], "eligible": 0}
            return [c for c in fallback if c.id != "more-actions"]
        vectors = self.encoder.embed([c.text for c in cards])
        embed_query = getattr(self.encoder, "embed_query", None)
        query_vector = embed_query(intent.query) if callable(embed_query) else self.encoder.embed([intent.query])[0]
        semantic = vectors @ query_vector
        query_words = set(re.findall(r"[a-z]{3,}", intent.positive))
        lexical = []
        for card in cards:
            words = set(re.findall(r"[a-z]{3,}", card.text.casefold()))
            exact = len(query_words & words)
            fuzzy = sum(any(one_edit(w, v) for v in words) for w in query_words - words if len(w) >= 4)
            lexical.append(exact + .4 * fuzzy)
        dense_order = list(np.argsort(-semantic, kind="stable"))
        lexical_order = sorted(range(len(cards)), key=lambda i: (-lexical[i], i))
        dense_ranks = {i: rank for rank, i in enumerate(dense_order)}
        lex_ranks = {i: rank for rank, i in enumerate(lexical_order)}
        fused = sorted(range(len(cards)), key=lambda i: -(1 / (60 + dense_ranks[i]) + 1 / (60 + lex_ranks[i])))
        groups: dict[str, list[int]] = {}
        for i in fused:
            if cards[i].group:
                groups.setdefault(cards[i].group, []).append(i)
        anchors = [indices[0] for _, indices in sorted(groups.items(), key=lambda pair: -max(cards[i].priority for i in pair[1])) if max(cards[i].priority for i in indices) >= 4]
        capacity = min(ceiling - len(fallback), max(self.nominal_limit - len(fallback), len(anchors)))
        chosen, seen = [], set()
        def add(i):
            c = cards[i].candidate
            # Deduplicate only identical executable launch entries.
            key = ("launch", json.dumps(thaw(c.arguments), sort_keys=True)) if c.id.startswith("launch-app-") else ("id", c.id)
            if key not in seen and len(chosen) < capacity:
                chosen.append(i)
                seen.add(key)
        for i in anchors:
            add(i)
        # Keep additional actions on the resolved controls (e.g. focus and
        # type). Their group cannot be displaced by unrelated dense hits.
        for i in sorted(fused, key=lambda i: -cards[i].priority):
            if cards[i].group and cards[i].priority >= 2:
                add(i)
        # One rescue when the intent is grounded; otherwise use the full
        # budget. Do not pad an accurate small set with arbitrary actions.
        rescue_budget = 1 if anchors and not intent.ambiguous else capacity
        before = len(chosen)
        for i in fused:
            if len(chosen) >= before + rescue_budget:
                break
            add(i)
        if page > 0:
            # Page from the tail excluded on page zero, so a short first page
            # cannot make the intervening candidates permanently unreachable.
            remaining = [
                i for i in fused
                if cards[i].candidate.id not in seen_candidate_ids
            ] if seen_candidate_ids else [i for i in fused if i not in chosen]
            chosen, seen = [], set()
            offset = 0 if seen_candidate_ids else (max(1, page) - 1) * capacity
            for i in remaining[offset:offset + capacity]:
                add(i)
        offered_ids = seen_candidate_ids | {cards[i].candidate.id for i in chosen}
        has_more = (
            offset + len(chosen) < len(remaining)
            if page > 0 and not seen_candidate_ids
            else any(card.candidate.id not in offered_ids for card in cards)
        )
        if not has_more:
            fallback = [c for c in fallback if c.id != "more-actions"]
        self.last_diagnostics = {
            "query": intent.query, "repairs": repairs, "ambiguous": intent.ambiguous,
            "targets": intent.targets, "forbidden": intent.forbidden,
            "routing": routing, "eligible": len(cards), "groups": len(groups),
            "protected_ids": [cards[i].candidate.id for i in anchors],
            "unrepresented_groups": [cards[i].group for i in anchors if i not in chosen],
            "total_options": len(chosen) + len(fallback),
            "has_more": has_more,
            "cache_sync": getattr(self.encoder, "last_sync", {}),
        }
        # Give Jev the same ownership and pane context used for retrieval.
        # IDs, arguments and bundle steps remain bound to the original action.
        return [replace(cards[i].candidate, description=cards[i].candidate.description + " Context: " + cards[i].text) for i in chosen] + fallback
