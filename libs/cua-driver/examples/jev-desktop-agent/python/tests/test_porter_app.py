from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from PySide6.QtCore import QRect
except ModuleNotFoundError as error:
    raise unittest.SkipTest(
        "PySide6 is an optional native-GUI dependency"
    ) from error

from porter_app import _clamp_window_position


class PorterAppTest(unittest.TestCase):
    def test_compact_window_position_is_kept_on_visible_screen(self):
        screens = [QRect(0, 0, 1920, 1080)]
        self.assertEqual(
            _clamp_window_position(400, 80, 700, 104, screens),
            (400, 80),
        )

    def test_compact_window_is_recovered_after_monitor_disconnect(self):
        screens = [QRect(0, 0, 1920, 1080)]
        self.assertEqual(
            _clamp_window_position(2500, -300, 700, 104, screens),
            (1220, 0),
        )

    def test_negative_origin_monitor_is_supported(self):
        screens = [QRect(-1920, 0, 1920, 1080), QRect(0, 0, 1920, 1080)]
        self.assertEqual(
            _clamp_window_position(-1700, 900, 700, 104, screens),
            (-1700, 900),
        )


if __name__ == "__main__":
    unittest.main()
