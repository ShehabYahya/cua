from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Element, Observation, PlanStep
from verifier import OpenRouterVerifier


class FailIfCalledClient:
    def chat_json(self, **kwargs):
        raise AssertionError("remote verifier should not be called")


class VerifierTest(unittest.TestCase):
    def test_text_readback_completes_locally(self):
        verifier = OpenRouterVerifier(FailIfCalledClient())
        observation = Observation(
            "s1",
            7,
            9,
            "Firefox",
            "New Tab",
            (
                Element(
                    1,
                    "s1:1",
                    "combo box",
                    "Search with Google or enter address",
                    value="Alan Turing",
                ),
            ),
        )
        result = asyncio.run(
            verifier.verify(
                original_goal="search for Alan Turing",
                step=PlanStep(
                    "Type the search query into the address bar",
                    app="Firefox",
                    text="Alan Turing",
                    completion="The address bar contains Alan Turing",
                ),
                observation=observation,
                history=[],
            )
        )
        self.assertTrue(result.done)
        self.assertEqual(result.confidence, 1.0)

    def test_new_tab_title_completes_locally(self):
        verifier = OpenRouterVerifier(FailIfCalledClient())
        observation = Observation(
            "s1",
            7,
            9,
            "Firefox",
            "New Tab — Mozilla Firefox",
            (),
        )
        result = asyncio.run(
            verifier.verify(
                original_goal="open a new tab",
                step=PlanStep(
                    "Open a new tab in Firefox",
                    app="Firefox",
                    completion="A new empty tab is open and focused in Firefox",
                ),
                observation=observation,
                history=[],
            )
        )
        self.assertTrue(result.done)


if __name__ == "__main__":
    unittest.main()
