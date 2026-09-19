from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from candidates import build_candidates
from contracts import Element, Observation
from jev_adapter import OPENROUTER_ENDPOINT, OpenRouterChooser, chooser_from_env


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class OpenRouterTest(unittest.TestCase):
    def observation(self) -> Observation:
        return Observation(
            "s1",
            7,
            9,
            "Browser",
            "Example",
            (Element(2, "s1:2", "Button", "Downloads"),),
        )

    def test_openrouter_uses_decisions_endpoint_and_keeps_arguments_local(self) -> None:
        observation = self.observation()
        candidates = build_candidates("click downloads", observation)
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(
                {
                    "model": "typesafe/jev-1.13-test",
                    "answers": {
                        "driver_action": {
                            "type": "choice",
                            "choice": "click-2",
                            "probabilities": {
                                "click-2": 0.97,
                                "reobserve": 0.02,
                                "abstain": 0.01,
                            },
                        }
                    },
                }
            )

        chooser = OpenRouterChooser(
            "test-only-token",
            model="~typesafe/jev-latest",
            opener=opener,
        )
        decision = asyncio.run(
            chooser.choose(
                goal="click downloads",
                observation=observation,
                candidates=candidates,
                history=[],
            )
        )

        self.assertEqual(captured["url"], OPENROUTER_ENDPOINT)
        self.assertEqual(captured["authorization"], "Bearer test-only-token")
        self.assertEqual(captured["body"]["model"], "~typesafe/jev-latest")
        self.assertEqual(
            captured["body"]["provider"],
            {
                "data_collection": "deny",
                "zdr": True,
                "allow_fallbacks": False,
            },
        )
        serialized = json.dumps(captured["body"])
        self.assertNotIn("element_token", serialized)
        self.assertNotIn("delivery_mode", serialized)
        self.assertEqual(decision.selected_id, "click-2")
        self.assertEqual(decision.confidence, 0.97)

    def test_auto_prefers_openrouter_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-only-token",
                "TYPESAFE_API_KEY": "also-present",
            },
            clear=True,
        ):
            chooser = chooser_from_env("auto")
        self.assertIsInstance(chooser, OpenRouterChooser)

    def test_missing_openrouter_key_fails_before_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                chooser_from_env("openrouter")


if __name__ == "__main__":
    unittest.main()
