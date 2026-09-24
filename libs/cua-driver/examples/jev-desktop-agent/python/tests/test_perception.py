from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Element, Observation, Rect, VisualRegion
from perception import OpenRouterVisionPerceiver


class FakeVisionClient:
    def __init__(self):
        self.calls = 0

    def chat_json(self, **kwargs):
        self.calls += 1
        return {
            "summary": "A custom button is visible.",
            "regions": [
                {
                    "id": "v1",
                    "label": "Custom Action",
                    "kind": "button",
                    "x": 10,
                    "y": 20,
                    "width": 100,
                    "height": 40,
                    "confidence": 0.95,
                }
            ],
        }


class PerceptionTest(unittest.TestCase):
    def _image(self):
        handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        handle.write(b"not-a-real-image-needed-by-fake-client")
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_explicit_vision_enrichment_uses_the_supplied_screenshot(self):
        client = FakeVisionClient()
        observation = Observation(
            "s1",
            7,
            9,
            "Demo",
            "Demo",
            (Element(1, "s1:1", "button", "Save"),),
            screenshot_path=self._image(),
            screenshot_width=400,
            screenshot_height=300,
        )
        result = asyncio.run(
            OpenRouterVisionPerceiver(client).enrich("click Save", observation)
        )
        # Lazy invocation is owned by AgentLoop. Once the perceiver is explicitly
        # invoked with a real screenshot, it performs one bounded request rather
        # than trying to infer that lexical semantic overlap is sufficient.
        self.assertEqual(client.calls, 1)
        self.assertEqual(result.visual_regions[0].label, "Custom Action")

    def test_no_screenshot_never_calls_remote_vision(self):
        client = FakeVisionClient()
        observation = Observation(
            "s1",
            7,
            9,
            "Demo",
            "Demo",
            (Element(1, "s1:1", "button", "Save"),),
        )
        result = asyncio.run(
            OpenRouterVisionPerceiver(client).enrich("click Save", observation)
        )
        self.assertEqual(client.calls, 0)
        self.assertEqual(result, observation)

    def test_calls_remote_vision_when_semantics_do_not_ground_goal(self):
        client = FakeVisionClient()
        observation = Observation(
            "s1",
            7,
            9,
            "Demo",
            "Demo",
            (Element(1, "s1:1", "label", "Status"),),
            screenshot_path=self._image(),
            screenshot_width=400,
            screenshot_height=300,
        )
        result = asyncio.run(
            OpenRouterVisionPerceiver(client).enrich(
                "click Custom Action",
                observation,
            )
        )
        self.assertEqual(client.calls, 1)
        self.assertEqual(result.visual_regions[0].label, "Custom Action")


if __name__ == "__main__":
    unittest.main()
