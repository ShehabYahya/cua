"""Synthetic contract tests; no model, Jev, or desktop interaction."""
import unittest
import asyncio
from dataclasses import replace

import numpy as np

from contracts import Candidate, Element, Observation, Rect, StepRecord
from retrieval import CandidateRetriever, Context, interpret, one_edit, repair, retrieval_query
from writer import PreparedText


class Encoder:
    def embed(self, texts):
        # Flat scores expose deterministic tie breaking and protected groups.
        return np.ones((len(texts), 2), dtype=np.float32)


def fallback():
    return [Candidate(i, i, None, {}, source="terminal") for i in ("done", "abstain", "reobserve", "more-actions")]


def obs(elements=()):
    return Observation("s1", 1, 2, "Firefox", "ChatGPT — Firefox", tuple(elements),
                       desktop_windows=({"pid": 3, "window_id": 4, "app_name": "ChatGPT", "title": "ChatGPT"},))


def action(e, tool="click"):
    return Candidate(f"{tool}-{e.index}", f"{tool} {e.label}", tool,
                     {"element_index": e.index, "element_token": e.token, "snapshot_id": "s1", "target": {"pid": 1, "window_id": 2}}, snapshot_id="s1")


class RetrievalTests(unittest.TestCase):
    def test_one_edit_and_ambiguous_correction(self):
        for value in ("firfox", "firefoxx", "firefx", "fireofx"):
            self.assertTrue(one_edit(value, "firefox"))
        self.assertFalse(one_edit("frfx", "firefox"))
        corrected, edits, ambiguous = repair("clsoe no fooo", {"close", "food", "foot"})
        self.assertEqual(corrected, "close no fooo")
        self.assertIn("fooo", ambiguous)

    def test_payload_and_url_are_not_repaired(self):
        slots = (PreparedText("t", "clsoe Firefx"), PreparedText("u", "https://chatgpt.com/"))
        q = retrieval_query('Type "clsoe Firefx" at chatgpt.com', slots)
        self.assertNotIn("clsoe", q)
        self.assertNotIn("chatgpt.com", q)
        self.assertEqual(slots[0].text, "clsoe Firefx")

    def test_negation_protects_correct_route(self):
        close = Element(1, "a", "button", "Close Firefox")
        switch = Candidate("switch-window-3-4", "Switch to ChatGPT", None, {"pid": 3, "window_id": 4}, source="session")
        retriever = CandidateRetriever(Encoder())
        chosen = retriever.select("Don't clsoe Firefx; close the ChatGTP desktop app.",
                                  [action(close), switch, *fallback()], observation=obs([close]))
        self.assertEqual(chosen[0].id, switch.id)
        self.assertNotIn("click-1", {c.id for c in chosen})
        self.assertEqual(retriever.last_diagnostics["protected_ids"], [switch.id])

    def test_prohibition_is_per_operation(self):
        close, open_tab = Element(1, "a", "button", "Close Firefox"), Element(2, "b", "button", "New tab")
        retriever = CandidateRetriever(Encoder())
        cards, _, _, _ = retriever.prepare("Don't close Firefox; open a tab in Firefox.",
                                          [action(close), action(open_tab)], obs([close, open_tab]))
        self.assertEqual([c.candidate.id for c in cards], ["click-2"])

    def test_ownership_and_fresh_token(self):
        e = Element(1, "new", "entry", "Ask ChatGPT")
        old = replace(action(e), arguments={**dict(action(e).arguments), "element_token": "old"})
        foreign = replace(action(e), id="foreign", arguments={**dict(action(e).arguments), "target": {"pid": 8, "window_id": 9}})
        self.assertFalse(Context(obs([e])).fresh(old))
        self.assertFalse(Context(obs([e])).fresh(foreign))
        self.assertTrue(Context(obs([e])).fresh(action(e)))

    def test_browser_refs_are_bound_to_fresh_tab(self):
        e = Element(1, None, "textbox", "Message", source="browser", browser_ref="r1")
        state = replace(obs([e]), browser_target_id="t", browser_tab_id="tab1")
        c = Candidate("browser-type", "Type message", "browser_type", {"ref": "r1", "target_id": "t", "tab_id": "tab1"})
        self.assertTrue(Context(state).fresh(c))
        self.assertFalse(Context(replace(state, browser_tab_id="tab2")).fresh(c))

    def test_window_frame_ownership(self):
        frame = Element(1, "a", "frame", "Other window")
        field = Element(2, "b", "entry", "Ask ChatGPT", parent_index=1)
        self.assertFalse(Context(obs([frame, field])).fresh(action(field)))

    def test_panes_are_contextual_and_protected(self):
        doc = Element(1, "a", "document web", "ChatGPT")
        left = Element(2, "b", "entry", "Ask ChatGPT", parent_index=1, bounds=Rect(0, 0, 10, 10))
        right = replace(left, index=3, token="c", bounds=Rect(100, 0, 10, 10))
        r = CandidateRetriever(Encoder())
        chosen = r.select('Type "hello" into the lefft ChatGPT chat box.',
                          [action(right), action(left, "type_text"), action(left), *fallback()],
                          observation=obs([doc, left, right]), texts=(PreparedText("t", "hello"),))
        self.assertIn(chosen[0].id, {"type_text-2", "click-2"})
        self.assertEqual(len(r.last_diagnostics["protected_ids"]), 1)

    def test_widening_preserves_groups(self):
        doc = Element(0, "doc", "document web", "ChatGPT")
        fields = [Element(i, str(i), "entry", f"Message {i}", parent_index=0) for i in range(1, 9)]
        r = CandidateRetriever(Encoder())
        chosen = r.select("Type hello in ChatGPT", [*(action(e) for e in fields), *fallback()], observation=obs([doc, *fields]))
        self.assertEqual(len(chosen), 11)
        self.assertFalse(r.last_diagnostics["unrepresented_groups"])

    def test_paging_covers_unselected_tail(self):
        elements = [Element(i, str(i), "button", f"Action {i}") for i in range(20)]
        pool = [*(action(e) for e in elements), *fallback()]
        r = CandidateRetriever(Encoder())
        seen = set()
        for page in range(4):
            seen.update(c.id for c in r.select("Choose a control", pool, observation=obs(elements), page=page))
        self.assertTrue({c.id for c in pool} <= seen)

    def test_fresh_actions_rebind_cached_descriptions(self):
        old = Element(1, "old", "entry", "Message")
        fresh = replace(old, token="fresh")
        r = CandidateRetriever(Encoder())
        r.select("Type hello", [action(old)], observation=obs([old]))
        result = r.select("Type hello", [action(fresh)], observation=obs([fresh]))
        self.assertEqual(result[0].arguments["element_token"], "fresh")

    def test_empty_pool_still_offers_recovery(self):
        result = CandidateRetriever(Encoder()).select("Close ChatGPT", fallback(), observation=obs())
        self.assertEqual({c.id for c in result}, {"done", "abstain", "reobserve"})

    def test_fresh_value_and_history_promote_search_submit(self):
        doc = Element(1, "a", "document web", "YouTube")
        field = Element(2, "b", "combo box", "Search", value="rust gameplay", parent_index=1)
        submit = Element(3, "c", "button", "Search", parent_index=1)
        state = replace(obs([doc, field, submit]), window_title="YouTube — Firefox")
        typing, search = action(field, "type_text"), action(submit)
        history = [StepRecord(1, 1, "s0", typing.id, typing.description, 1, True, "executed")]
        result = CandidateRetriever(Encoder()).select("Search YouTube for rust gameplay",
            [typing, search, *fallback()], observation=state,
            texts=(PreparedText("t", "rust gameplay"),), history=history)
        self.assertEqual(result[0].id, search.id)
        # Successful history alone does not prove that the current field is full.
        empty_state = replace(state, elements=(doc, replace(field, value=""), submit))
        result = CandidateRetriever(Encoder()).select("Search YouTube for rust gameplay",
            [typing, search, *fallback()], observation=empty_state,
            texts=(PreparedText("t", "rust gameplay"),), history=history)
        self.assertEqual(result[0].id, typing.id)

    def test_shortlist_entry_point_passes_retrieval_context(self):
        from candidates import shortlist_candidates
        field = Element(1, "a", "entry", "Message")
        result = shortlist_candidates("Type hello", [action(field), *fallback()],
            retriever=CandidateRetriever(Encoder()), observation=obs([field]),
            prepared_texts=(PreparedText("t", "hello"),))
        self.assertIn("click-1", {c.id for c in result})
        with self.assertRaises(ValueError):
            shortlist_candidates("Type hello", fallback(), retriever=CandidateRetriever(Encoder()))

    def test_valid_instruction_words_are_not_ui_spellings(self):
        e = Element(1, "a", "button", "Edit shot next")
        r = CandidateRetriever(Encoder())
        for word in ("exit", "shut", "closed"):
            _, intent, repairs, _ = r.prepare(f"{word} the ChatGPT desktop app", [action(e)], obs([e]))
            self.assertTrue(intent.closing)
            self.assertEqual(intent.targets, ("chatgpt",))
            self.assertFalse(repairs)
        _, intent, repairs, _ = r.prepare("Search YouTube for rust gameplay", [action(e)], obs([e]), (PreparedText("t", "rust gameplay"),))
        self.assertIn("supplied text", intent.query)
        self.assertFalse(repairs)

    def test_typing_does_not_protect_search_buttons(self):
        doc = Element(1, "a", "document web", "ChatGPT")
        field = Element(2, "b", "entry", "Ask ChatGPT", parent_index=1)
        search = Element(3, "c", "button", "Search", parent_index=1)
        r = CandidateRetriever(Encoder())
        cards, _, _, _ = r.prepare("Type hello in ChatGPT", [action(field), action(search)], obs([doc, field, search]), (PreparedText("t", "hello"),))
        self.assertIsNone(next(c.group for c in cards if c.element.index == 3))

    def test_returned_context_keeps_execution_arguments(self):
        e = Element(1, "a", "entry", "Message")
        original = action(e)
        result = CandidateRetriever(Encoder()).select("Type hello", [original], observation=obs([e]))[0]
        self.assertEqual(result.id, original.id)
        self.assertEqual(result.arguments, original.arguments)
        self.assertIn("app firefox", result.description)

    def test_agent_loop_supplies_fresh_state_history_and_original_goal(self):
        from loop import AgentLoop
        from contracts import Decision
        from tests.test_loop import FakeDriver
        class CapabilityChooser:
            async def choose(inner, *, observation, candidates, **kwargs):
                selected = "done" if observation.window_title == "Search the web" else "hotkey-new-tab"
                self.assertIn(selected, {c.id for c in candidates})
                return Decision(selected, .99, {selected: .99})
        class RecordingRetriever(CandidateRetriever):
            def __init__(self):
                super().__init__(Encoder())
                self.calls = []
            def select(self, goal, candidates, **kwargs):
                self.calls.append((goal, kwargs["observation"].snapshot_id, tuple(kwargs["history"])))
                return super().select(goal, candidates, **kwargs)
        driver, retriever = FakeDriver(), RecordingRetriever()
        result = asyncio.run(AgentLoop(driver, CapabilityChooser(), retriever=retriever, max_steps=4).run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(driver.executed, ["hotkey-new-tab"])
        self.assertEqual([(g, s) for g, s, _ in retriever.calls], [("open new tab", "s1"), ("open new tab", "s2")])
        self.assertEqual(len(retriever.calls[1][2]), 1)


if __name__ == "__main__":
    unittest.main()
