from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from candidates import build_candidates
from contracts import Element, Observation
from jev_adapter import TypeSafeChooser
from writer import quoted_text_slots, redact_prepared_text


class FakeClient:
    sent = None

    def system_one(self, **payload):
        self.sent = payload
        return SimpleNamespace(
            model="jev-test",
            choices={
                "driver_action": SimpleNamespace(
                    choice="click-2",
                    confidence=0.91,
                    probabilities={
                        "click-2": 0.91,
                        "reobserve": 0.05,
                        "abstain": 0.04,
                    },
                )
            },
        )


class JevAdapterTest(unittest.TestCase):
    def test_provider_sees_ids_and_descriptions_not_executable_arguments(self) -> None:
        observation = Observation(
            "s1",
            7,
            9,
            "Browser",
            "Example",
            (
                Element(2, "s1:2", "Button", "Search"),
                Element(3, "s1:3", "TextField", "Query"),
            ),
        )
        candidates = build_candidates(
            'search "secret local text"',
            observation,
            prepared_texts=quoted_text_slots('search "secret local text"'),
        )
        client = FakeClient()
        chooser = TypeSafeChooser(client)
        fake_sdk = SimpleNamespace(
            Choice=lambda **kwargs: SimpleNamespace(**kwargs),
            TypeSafeClient=object,
        )
        with patch.dict(sys.modules, {"typesafe_sdk": fake_sdk}):
            decision = asyncio.run(
                chooser.choose(
                    goal=redact_prepared_text(
                        'search "secret local text"',
                        quoted_text_slots('search "secret local text"'),
                    ),
                    observation=observation,
                    candidates=candidates,
                    history=[],
                )
            )
        self.assertEqual(decision.selected_id, "click-2")
        serialized = json.dumps(client.sent["state"])
        self.assertNotIn("element_token", serialized)
        self.assertNotIn("delivery_mode", serialized)
        self.assertNotIn("secret local text", json.dumps(client.sent, default=str))
        self.assertIn("<text-1>", client.sent["state"]["goal"])


if __name__ == "__main__":
    unittest.main()
