from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import Observation
from loop import AgentLoop


class FakeDriver:
    coordinate_click_supported = True
    capture_bound_click = False


class VisualClickModeTest(unittest.TestCase):
    def _observation(self):
        return Observation(
            snapshot_id="s1",
            pid=1,
            window_id=2,
            app="Test",
            window_title="Test",
            elements=(),
        )

    def test_strict_requires_capture_binding(self):
        agent = AgentLoop(
            FakeDriver(),
            object(),
            visual_click_mode="strict",
        )
        seen = {}

        def fake_build(*args, **kwargs):
            seen.update(kwargs)
            return []

        with patch("loop.build_candidates", fake_build):
            agent._build_pool("click it", self._observation(), ())

        self.assertFalse(seen["allow_visual_clicks"])

    def test_permissive_accepts_driver_coordinate_support(self):
        agent = AgentLoop(
            FakeDriver(),
            object(),
            visual_click_mode="permissive",
        )
        seen = {}

        def fake_build(*args, **kwargs):
            seen.update(kwargs)
            return []

        with patch("loop.build_candidates", fake_build):
            agent._build_pool("click it", self._observation(), ())

        self.assertTrue(seen["allow_visual_clicks"])


if __name__ == "__main__":
    unittest.main()
