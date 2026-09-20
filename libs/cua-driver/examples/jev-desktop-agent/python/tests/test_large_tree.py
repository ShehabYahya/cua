from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from candidates import build_candidates
from contracts import Candidate, Decision, Element, Observation
from jev_adapter import HierarchicalChooser, _state
from loop import AgentLoop
from test_direct_search_workflow import DirectSearchDriver
from verifier import state_changed
from writer import inferred_text_slots, redact_prepared_text

GOAL = (
    "Open Firefox, open a new tab, search the web for Alan Turing, "
    "and stop when the search results are visible"
)


def large_observation():
    # Real browsers expose menus before the active tab and editable controls.
    menus = tuple(
        Element(i, f"s1:{i}", "menu item", f"Open search results {i}") for i in range(1800)
    )
    return Observation(
        "s1",
        7,
        9,
        "Mozilla Firefox",
        "Mozilla Firefox",
        menus
        + (
            Element(1800, "s1:1800", "page tab", "New Tab", selected=True),
            Element(1801, "s1:1801", "entry", "Address", value=""),
        ),
    )


class LargeTreeTest(unittest.TestCase):
    def test_search_shortcuts_use_native_modifier(self):
        for platform, modifier in (("linux", "ctrl"), ("win32", "ctrl"), ("darwin", "cmd")):
            with self.subTest(platform=platform), patch("candidates.sys.platform", platform):
                candidates = build_candidates(GOAL, large_observation())
                for cid, key in (("hotkey-new-tab", "t"), ("hotkey-address", "l")):
                    candidate = next(c for c in candidates if c.id == cid)
                    self.assertEqual(candidate.arguments["keys"], (modifier, key))

    def test_typing_and_recovery_survive_both_candidate_budgets(self):
        observation = large_observation()
        slots = inferred_text_slots(GOAL)
        candidates = build_candidates(
            GOAL,
            observation,
            prepared_texts=slots,
            max_candidates=96,
        )
        outer = self

        class InspectChooser:
            async def choose(self, *, candidates, **kwargs):
                ids = {c.id for c in candidates}
                outer.assertLessEqual(len(candidates), 32)
                outer.assertTrue(
                    {
                        "hotkey-address",
                        "press-enter",
                        "type-focused-text-1",
                        "type-1801-text-1",
                        "done",
                        "reobserve",
                        "abstain",
                    }
                    <= ids,
                    f"Missing routes: {sorted(ids)}",
                )
                state = _state(**kwargs, candidates=candidates)
                elements = state["observation"]["elements"]
                outer.assertLessEqual(len(elements), 96)
                outer.assertTrue({1800, 1801} <= {e["index"] for e in elements})
                outer.assertNotIn("Alan Turing", str(state))
                return Decision("type-1801-text-1", 0.9, {"type-1801-text-1": 0.9})

        decision = asyncio.run(
            HierarchicalChooser(InspectChooser()).choose(
                goal=redact_prepared_text(GOAL, slots),
                observation=observation,
                candidates=candidates,
                history=[],
            )
        )
        self.assertEqual(decision.selected_id, "type-1801-text-1")

    def test_typing_beyond_first_250_elements_counts_as_progress(self):
        before = large_observation()
        after = replace(
            before,
            snapshot_id="s2",
            elements=before.elements[:-1] + (replace(before.elements[-1], value="Alan Turing"),),
        )
        self.assertTrue(state_changed(before, after))
        self.assertFalse(state_changed(before, replace(before, snapshot_id="s3")))

    def test_candidate_state_keeps_refs_and_exposes_bounded_ordinary_field_text(self):
        observation = large_observation()
        field = Element(
            100001,
            None,
            "textbox",
            "Query",
            value="private text",
            source="browser",
            browser_ref="p1:8",
        )
        observation = replace(observation, elements=observation.elements + (field,))
        candidate = Candidate("browser-type", "Fill Query", "browser_type", {"ref": "p1:8"})
        state = _state("fill query", observation, [], candidates=[candidate])
        items = state["observation"]["elements"]
        self.assertIn(field.index, {e["index"] for e in items})
        compact_field = next(e for e in items if e["index"] == field.index)
        self.assertEqual(compact_field["value"], "<set>")
        observed = next(
            item
            for item in state["observed_field_text"]
            if item["index"] == field.index
        )
        self.assertEqual(observed["value"], "private text")
        self.assertNotIn("private text", candidate.description)
        self.assertNotIn("private text", str(dict(candidate.arguments)))

    def test_browser_search_shortcut_is_not_offered_in_unrelated_apps(self):
        observation = replace(large_observation(), app="Text Editor")
        ids = {c.id for c in build_candidates("search for Alan Turing", observation)}
        self.assertNotIn("hotkey-address", ids)

    def test_large_tree_search_loop_uses_bounded_masked_state(self):
        class LargeSearchDriver(DirectSearchDriver):
            async def observe(self, *args, **kwargs):
                observation = await super().observe(*args, **kwargs)
                menus = tuple(
                    Element(
                        500 + i,
                        f"{observation.snapshot_id}:{500 + i}",
                        "menu item",
                        f"Open search results {i}",
                    )
                    for i in range(1800)
                )
                return replace(observation, elements=menus + observation.elements)

        outer = self

        class StateChooser:
            async def choose(self, **kwargs):
                state = _state(**kwargs)
                compact = state["observation"]
                if "Google Search" in compact["window"]:
                    selected = "done"
                else:
                    field_text = next(
                        (
                            item["value"]
                            for item in state["observed_field_text"]
                            if item["index"] == 20
                        ),
                        "",
                    )
                    if field_text == "Alan Turing":
                        selected = "press-enter"
                    elif compact["window"].startswith("New Tab"):
                        selected = "type-focused-text-1"
                    else:
                        selected = "hotkey-new-tab"
                outer.assertIn(selected, {c.id for c in kwargs["candidates"]})
                return Decision(selected, 0.9, {selected: 0.9})

        driver = LargeSearchDriver()
        agent = AgentLoop(
            driver,
            HierarchicalChooser(StateChooser()),
            max_steps=8,
        )
        result = asyncio.run(agent.run(GOAL, act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual(driver.executed, ["hotkey-new-tab", "type-focused-text-1", "press-enter"])
        self.assertEqual(result.steps[1].reason, "state changed after the action")


if __name__ == "__main__":
    unittest.main()
