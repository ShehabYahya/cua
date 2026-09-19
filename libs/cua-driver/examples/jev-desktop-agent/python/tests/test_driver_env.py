from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from driver import driver_child_environment


class DriverEnvironmentTest(unittest.TestCase):
    def test_linux_defaults_to_is_enabled_only(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.dict(os.environ, {}, clear=True):
            env = driver_child_environment()
        self.assertEqual(
            env["CUA_DRIVER_RS_A11Y_ADVERTISE_MODE"],
            "is_enabled_only",
        )

    def test_explicit_advertise_mode_is_preserved(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.dict(
            os.environ,
            {"CUA_DRIVER_RS_A11Y_ADVERTISE_MODE": "none"},
            clear=True,
        ):
            env = driver_child_environment()
        self.assertEqual(env["CUA_DRIVER_RS_A11Y_ADVERTISE_MODE"], "none")


if __name__ == "__main__":
    unittest.main()
