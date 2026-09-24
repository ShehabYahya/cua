from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from PySide6.QtCore import QRect
except ModuleNotFoundError as error:
    raise unittest.SkipTest(
        "PySide6 is an optional native-GUI dependency"
    ) from error

from porter_app import _clamp_window_position, _sync_tray_actions


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

    def test_tray_stop_and_listen_actions_follow_runtime_state(self):
        class FakeAction:
            def __init__(self):
                self.text = ""
                self.enabled = True
                self.checked = False
                self.blocked = False

            def setText(self, value):
                self.text = value

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setChecked(self, value):
                self.checked = bool(value)

            def blockSignals(self, value):
                self.blocked = bool(value)

        stop = FakeAction()
        listen = FakeAction()
        porter = SimpleNamespace(
            busy=True,
            cancelling=False,
            handsFreeActive=False,
            manualVoiceActive=False,
        )

        _sync_tray_actions(porter, stop, listen)
        self.assertTrue(stop.enabled)
        self.assertEqual(stop.text, "Stop current task")
        self.assertFalse(listen.enabled)

        porter.busy = False
        _sync_tray_actions(porter, stop, listen)
        self.assertTrue(listen.enabled)

        porter.busy = True
        porter.cancelling = True
        _sync_tray_actions(porter, stop, listen)
        self.assertFalse(stop.enabled)
        self.assertEqual(stop.text, "Stopping current task…")
        self.assertFalse(listen.enabled)

        porter.busy = False
        porter.cancelling = False
        porter.manualVoiceActive = True
        _sync_tray_actions(porter, stop, listen)
        self.assertFalse(stop.enabled)
        self.assertFalse(listen.enabled)

    def test_negative_origin_monitor_is_supported(self):
        screens = [QRect(-1920, 0, 1920, 1080), QRect(0, 0, 1920, 1080)]
        self.assertEqual(
            _clamp_window_position(-1700, 900, 700, 104, screens),
            (-1700, 900),
        )


if __name__ == "__main__":
    unittest.main()
